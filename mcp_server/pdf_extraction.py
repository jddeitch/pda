"""
PDF extraction with fallback chain.

Per Part 5 of the plan:
- PRIMARY: PyMuPDF (fitz) — fast, handles most single-column PDFs well
- FALLBACK 1: pdfminer.six — better layout analysis for two-column academic papers
- FALLBACK 2: pdfplumber — good for table extraction, different text flow algorithm
- FLAG: PDFEXTRACT if all extractors fail — human preprocesses manually

This module detects SPECIFIC, OBSERVABLE problems (not confidence scores):
- BLOCKING: UNUSABLE, TOOSHORT, GARBLED
- WARNING: COLUMNJUMBLE, NOPARAGRAPHS, REPEATEDTEXT, NOREFSSECTION
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

import fitz  # PyMuPDF
from pdfminer.high_level import extract_text as pdfminer_extract
import pdfplumber

logger = logging.getLogger(__name__)


# --- Paths ---

PROJECT_ROOT = Path(__file__).parent.parent
CACHE_DIR = PROJECT_ROOT / "cache" / "articles"


@dataclass
class ExtractionResult:
    """Result of PDF text extraction."""
    text: str
    extractor_used: str
    problems: list[str]
    usable: bool


# --- Extraction Functions ---

def extract_pymupdf(pdf_path: Path) -> str:
    """
    Extract text using PyMuPDF (fitz).

    Fast, handles most single-column PDFs well.
    """
    doc = fitz.open(pdf_path)
    text_parts = []
    for page in doc:
        text_parts.append(page.get_text())
    doc.close()
    return "\n".join(text_parts)


def extract_pdfminer(pdf_path: Path) -> str:
    """
    Extract text using pdfminer.six.

    Better layout analysis for two-column academic papers.
    """
    return pdfminer_extract(str(pdf_path))


def extract_pdfplumber(pdf_path: Path) -> str:
    """
    Extract text using pdfplumber.

    Good for table extraction, different text flow algorithm.
    """
    text_parts = []
    with pdfplumber.open(pdf_path) as pdf:
        for page in pdf.pages:
            page_text = page.extract_text()
            if page_text:
                text_parts.append(page_text)
    return "\n".join(text_parts)


# --- Problem Detection ---

def detect_extraction_problems(text: str) -> list[str]:
    """
    Detect SPECIFIC, OBSERVABLE problems in extracted text.

    Per Part 5.2 of the plan: No scores — just problem codes
    that describe what's wrong.

    BLOCKING problems (cannot proceed):
    - UNUSABLE: Combined flag when text is not usable
    - TOOSHORT: Less than 100 words extracted
    - GARBLED: >5% garbage/encoding characters

    WARNING problems (proceed but flag):
    - COLUMNJUMBLE: Lines avg <40 chars — likely column detection issue
    - NOPARAGRAPHS: No paragraph breaks detected
    - REPEATEDTEXT: Same text block appears multiple times
    - NOREFSSECTION: Long article missing references (possible truncation)
    """
    problems: list[str] = []
    words = text.split()

    # BLOCKING: Too short to be a real article
    if len(words) < 100:
        problems.append("UNUSABLE")
        problems.append("TOOSHORT")
        return problems  # No point checking further

    # BLOCKING: Majority garbage characters (encoding failure)
    garbage_chars = set('\ufffd\u2588\u2591\u2592\u2593\x00')
    garbage_count = sum(1 for c in text if c in garbage_chars)
    if garbage_count > len(text) * 0.05:  # >5% garbage
        problems.append("UNUSABLE")
        problems.append("GARBLED")
        return problems

    # WARNING: Column jumbling (lines too short = bad layout detection)
    lines = [line for line in text.split('\n') if line.strip()]
    if lines:
        avg_line_length = sum(len(line) for line in lines) / len(lines)
        if avg_line_length < 40:
            problems.append("COLUMNJUMBLE")

    # WARNING: No paragraph structure (everything ran together)
    paragraphs = [p for p in text.split('\n\n') if p.strip()]
    if len(paragraphs) < 3 and len(words) > 500:
        problems.append("NOPARAGRAPHS")

    # WARNING: Repeated text blocks (extraction loop bug)
    if _has_repeated_blocks(text):
        problems.append("REPEATEDTEXT")

    # WARNING: References section missing (possible truncation)
    if len(words) > 2000 and not any(
        marker in text.lower()
        for marker in ['references', 'bibliography', 'works cited', 'références', 'bibliographie']
    ):
        problems.append("NOREFSSECTION")

    return problems


def _has_repeated_blocks(text: str, min_block_size: int = 100) -> bool:
    """
    Detect if the same text block appears multiple times.

    Indicates extraction bug (common with headers/footers).
    """
    # Split into chunks and look for duplicates
    chunks = [
        text[i:i + min_block_size]
        for i in range(0, len(text) - min_block_size, min_block_size)
    ]
    seen: set[str] = set()
    for chunk in chunks:
        normalized = ' '.join(chunk.split())  # Normalize whitespace
        if normalized in seen:
            return True
        seen.add(normalized)
    return False


# --- Main Extraction Function ---

def extract_article_text(article_path: Path) -> ExtractionResult:
    """
    Extract text from a PDF using fallback chain.

    Tries each extractor in order, returns first usable result.
    Records which extractor succeeded and any problems detected.

    Args:
        article_path: Path to PDF file (or .txt for preprocessed)

    Returns:
        ExtractionResult with text, extractor used, problems, and usability flag
    """
    # Check for preprocessed .txt file first (per D19)
    txt_path = article_path.with_suffix('.txt')
    if txt_path.exists():
        logger.info(f"Using preprocessed text: {txt_path}")
        text = txt_path.read_text(encoding='utf-8')
        problems = detect_extraction_problems(text)
        return ExtractionResult(
            text=text,
            extractor_used="preprocessed",
            problems=[p for p in problems if p != "UNUSABLE"],  # Preprocessed is trusted
            usable=True
        )

    # Handle HTML files
    if article_path.suffix.lower() == '.html':
        return _extract_from_html(article_path)

    # PDF extraction with fallback chain
    extractors: list[tuple[str, Callable[[Path], str]]] = [
        ("pymupdf", extract_pymupdf),
        ("pdfminer", extract_pdfminer),
        ("pdfplumber", extract_pdfplumber),
    ]

    for name, extract_fn in extractors:
        try:
            logger.info(f"Trying extractor: {name}")
            text = extract_fn(article_path)
            problems = detect_extraction_problems(text)

            # If no BLOCKING problems, use this extraction
            if "UNUSABLE" not in problems:
                logger.info(f"Extraction successful with {name}, problems: {problems}")
                return ExtractionResult(
                    text=text,
                    extractor_used=name,
                    problems=problems,
                    usable=True
                )
            else:
                logger.warning(f"Extractor {name} produced unusable text: {problems}")

        except Exception as e:
            logger.warning(f"Extractor {name} failed: {e}")
            continue

    # All extractors failed
    logger.error(f"All extractors failed for {article_path}")
    return ExtractionResult(
        text="",
        extractor_used="none",
        problems=["PDFEXTRACT"],
        usable=False
    )


def _extract_from_html(html_path: Path) -> ExtractionResult:
    """
    Extract text from HTML file.

    Basic HTML-to-text conversion for cached web pages.
    """
    from html.parser import HTMLParser

    class TextExtractor(HTMLParser):
        def __init__(self):
            super().__init__()
            self.text_parts: list[str] = []
            self.skip_tags = {'script', 'style', 'nav', 'header', 'footer'}
            self.current_skip = 0

        def handle_starttag(self, tag: str, attrs):
            if tag in self.skip_tags:
                self.current_skip += 1

        def handle_endtag(self, tag: str):
            if tag in self.skip_tags and self.current_skip > 0:
                self.current_skip -= 1

        def handle_data(self, data: str):
            if self.current_skip == 0:
                text = data.strip()
                if text:
                    self.text_parts.append(text)

    try:
        html_content = html_path.read_text(encoding='utf-8')
        parser = TextExtractor()
        parser.feed(html_content)
        text = '\n\n'.join(parser.text_parts)

        problems = detect_extraction_problems(text)
        return ExtractionResult(
            text=text,
            extractor_used="html",
            problems=problems,
            usable="UNUSABLE" not in problems
        )
    except Exception as e:
        logger.error(f"HTML extraction failed: {e}")
        return ExtractionResult(
            text="",
            extractor_used="none",
            problems=["PDFEXTRACT"],
            usable=False
        )


# --- Cache Path Utilities (per D19) ---

def get_cached_path(article_id: str) -> Path | None:
    """
    Return path to cached content, or None if not cached.

    Checks for multiple formats in order of preference:
    1. .txt (preprocessed — takes precedence)
    2. .pdf
    3. .html
    """
    for ext in ['.txt', '.pdf', '.html']:
        path = CACHE_DIR / f"{article_id}{ext}"
        if path.exists():
            return path
    return None


def cache_content(article_id: str, content: bytes, source_url: str) -> Path:
    """
    Save fetched content to cache.

    Extension determined by content type or URL.
    """
    # Ensure cache directory exists
    CACHE_DIR.mkdir(parents=True, exist_ok=True)

    # Determine extension
    if source_url.endswith('.pdf') or content[:4] == b'%PDF':
        ext = '.pdf'
    elif b'<html' in content[:1000].lower():
        ext = '.html'
    else:
        ext = '.txt'

    path = CACHE_DIR / f"{article_id}{ext}"
    path.write_bytes(content)
    logger.info(f"Cached content: {path}")
    return path


# --- PDF Metadata Extraction (for ingest_article) ---

def extract_pdf_metadata(pdf_path: Path) -> dict[str, str | None]:
    """
    Extract metadata from PDF using PyMuPDF.

    Falls back to text extraction for title if metadata missing.
    Used by ingest_article() tool.
    """
    doc = fitz.open(pdf_path)
    metadata = doc.metadata or {}

    result: dict[str, str | None] = {
        "title": metadata.get("title"),
        "author": metadata.get("author"),
        "subject": metadata.get("subject"),
        "keywords": metadata.get("keywords"),
        "creator": metadata.get("creator"),  # Often contains journal name
    }

    # If no title in metadata, try first page header
    if not result["title"] and doc.page_count > 0:
        first_page = doc[0].get_text()
        result["title"] = _extract_title_from_text(first_page)

    # Look for DOI in first page
    if doc.page_count > 0:
        first_page = doc[0].get_text()
        doi_match = re.search(r'10\.\d{4,}/[^\s]+', first_page)
        if doi_match:
            result["doi"] = doi_match.group(0).rstrip('.')
        else:
            result["doi"] = None

    doc.close()
    return result


def _extract_title_from_text(text: str) -> str | None:
    """
    Extract title from first page text.

    Heuristic: First non-empty line that's reasonable length.
    """
    lines = [line.strip() for line in text.split('\n') if line.strip()]
    for line in lines[:10]:  # Check first 10 non-empty lines
        # Skip very short lines (likely headers/page numbers)
        if len(line) < 10:
            continue
        # Skip lines that look like metadata
        if any(marker in line.lower() for marker in ['doi:', 'issn:', 'vol.', 'volume', 'journal']):
            continue
        # Skip lines that are all caps (often section headers)
        if line.isupper():
            continue
        # This might be the title
        if 10 < len(line) < 200:
            return line
    return None
