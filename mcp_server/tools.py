"""
MCP Tool implementations for the Translation Machine.

Phase 1 tools:
- get_next_article() — returns next article + taxonomy + workflow reminder
- get_progress() — returns status counts
- skip_article() — skip an article with reason
- set_human_review_interval() — configure review interval
- reset_session_counter() — reset after human review

Phase 2 tools:
- get_chunk() — get a chunk of article text for translation

Later phases will add:
- validate_classification()
- save_article()
- ingest_article()
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta
from typing import Any

from .database import get_database
from .taxonomy import get_taxonomy
from .glossary import find_glossary_terms_in_text
from .pdf_extraction import (
    extract_article_text,
    get_cached_path,
    CACHE_DIR,
)

logger = logging.getLogger(__name__)


# --- spaCy Model Loading (per D18) ---
# Load models ONCE at module import, not per-request.
# Takes ~2-3 seconds on first import; reused for all subsequent calls.

_nlp_en = None

def _get_nlp_en():
    """Get English spaCy model, loading it if necessary."""
    global _nlp_en
    if _nlp_en is None:
        try:
            import spacy
            _nlp_en = spacy.load("en_core_web_sm")
            logger.info("Loaded spaCy model: en_core_web_sm")
        except OSError:
            logger.warning(
                "spaCy model 'en_core_web_sm' not found. "
                "Long paragraphs won't be split at sentence boundaries. "
                "Run: python -m spacy download en_core_web_sm"
            )
    return _nlp_en


# --- Chunk Cache (per D26) ---
# In-memory cache with 1-hour TTL, cleared after save/skip
# Stores: chunks, timestamp, extractor_used, extraction_problems

from dataclasses import dataclass


@dataclass
class ChunkCacheEntry:
    """Cached extraction result for an article."""
    chunks: list[str]
    cached_at: datetime
    extractor_used: str
    extraction_problems: list[str]


_chunk_cache: dict[str, ChunkCacheEntry] = {}
CHUNK_CACHE_TTL = timedelta(hours=1)


def _get_cached_entry(article_id: str) -> ChunkCacheEntry | None:
    """Get cache entry if still valid."""
    if article_id in _chunk_cache:
        entry = _chunk_cache[article_id]
        if datetime.now() - entry.cached_at < CHUNK_CACHE_TTL:
            return entry
        # Expired — remove
        del _chunk_cache[article_id]
    return None


def _set_cached_entry(
    article_id: str,
    chunks: list[str],
    extractor_used: str,
    extraction_problems: list[str]
) -> None:
    """Store extraction result in cache."""
    _chunk_cache[article_id] = ChunkCacheEntry(
        chunks=chunks,
        cached_at=datetime.now(),
        extractor_used=extractor_used,
        extraction_problems=extraction_problems,
    )


def clear_chunk_cache(article_id: str | None = None) -> None:
    """
    Clear chunk cache.

    Called after save_article() or skip_article().
    If article_id is None, clears entire cache.
    """
    if article_id:
        _chunk_cache.pop(article_id, None)
    else:
        _chunk_cache.clear()


# --- Chunking Logic (per D3, Part 4.2) ---

def _split_into_chunks(text: str, target_paragraphs: int = 4) -> list[str]:
    """
    Split text into chunks of ~4 paragraphs each.

    Per D3:
    - Split on double newlines
    - Target 4 paragraphs per chunk
    - If a paragraph exceeds 500 words, split at ~400 words on sentence boundary

    Uses spaCy for sentence boundary detection on long paragraphs.
    """
    # Use module-level spaCy loader (per D18)
    nlp_en = _get_nlp_en()

    paragraphs = [p.strip() for p in text.split('\n\n') if p.strip()]
    chunks: list[str] = []
    current_chunk: list[str] = []

    for para in paragraphs:
        word_count = len(para.split())

        # If paragraph is too long (>500 words), split it
        if word_count > 500 and nlp_en is not None:
            sub_paras = _split_long_paragraph(para, nlp_en, target_words=400)
            for sub in sub_paras:
                current_chunk.append(sub)
                if len(current_chunk) >= target_paragraphs:
                    chunks.append('\n\n'.join(current_chunk))
                    current_chunk = []
        else:
            current_chunk.append(para)
            if len(current_chunk) >= target_paragraphs:
                chunks.append('\n\n'.join(current_chunk))
                current_chunk = []

    # Don't forget remaining paragraphs
    if current_chunk:
        chunks.append('\n\n'.join(current_chunk))

    return chunks


def _split_long_paragraph(para: str, nlp, target_words: int = 400) -> list[str]:
    """
    Split a long paragraph at sentence boundaries.

    Args:
        para: The paragraph text
        nlp: spaCy language model
        target_words: Target words per sub-paragraph

    Returns:
        List of sub-paragraphs
    """
    doc = nlp(para)
    sentences = list(doc.sents)

    sub_paras: list[str] = []
    current_sub: list[str] = []
    current_word_count = 0

    for sent in sentences:
        sent_text = sent.text.strip()
        sent_words = len(sent_text.split())

        if current_word_count + sent_words > target_words and current_sub:
            # Start a new sub-paragraph
            sub_paras.append(' '.join(current_sub))
            current_sub = [sent_text]
            current_word_count = sent_words
        else:
            current_sub.append(sent_text)
            current_word_count += sent_words

    # Don't forget remaining sentences
    if current_sub:
        sub_paras.append(' '.join(current_sub))

    return sub_paras


# --- Chunk Instruction (repeated per chunk to prevent context decay) ---

CHUNK_INSTRUCTION = """Translate this chunk faithfully. Match the author's register and style.
- Use glossary terms exactly as provided
- Preserve sentence structure where natural in French
- Do not add, remove, or "improve" content
- Note any tables (TBL), figures (FIG), or unclear passages (AMBIG) as flags"""


# Workflow reminder included in every get_next_article() response
# This prevents context decay per Part 3 of the plan
WORKFLOW_REMINDER = """1. Translate title and summary FIRST (even for paywalled articles)
2. IF open_access: call get_chunk() in loop until complete
3. Call validate_classification() with method, voice, peer_reviewed, categories, keywords
4. Call save_article() with validation_token"""


def get_next_article() -> dict[str, Any]:
    """
    Returns next article needing work, plus fresh taxonomy data.

    Prioritizes in_progress (crash recovery) over pending.
    Checks human_review_interval — returns SESSION_PAUSE if limit reached.

    Response schemas:

    SUCCESS:
    {
        "article": {
            "id": "post-id-16054",
            "source_title": "...",
            "source_url": "...",
            "summary_original": "...",
            "open_access": true,
            "doi": "10.1234/..." | null
        },
        "progress": {"pending": 47, "in_progress": 1, "translated": 2, "skipped": 0},
        "taxonomy": {"methods": [...], "voices": [...], "categories": [...]},
        "workflow_reminder": "1. Translate title+summary..."
    }

    SESSION_PAUSE:
    {
        "status": "SESSION_PAUSE",
        "articles_processed": 5,
        "message": "Human review interval reached. Please review in /admin before continuing."
    }

    COMPLETE:
    {
        "status": "COMPLETE",
        "translated": 52,
        "skipped": 3,
        "message": "All articles processed."
    }
    """
    db = get_database()
    taxonomy = get_taxonomy()

    # Check session limit first
    if db.check_session_limit():
        state = db.get_session_state()
        return {
            "status": "SESSION_PAUSE",
            "articles_processed": state["articles_processed_count"],
            "message": "Human review interval reached. Please review in /admin before continuing.",
        }

    # Get next article
    article = db.get_next_article()

    if article is None:
        # Check if all done or all skipped
        progress = db.get_progress()
        if progress["pending"] == 0 and progress["in_progress"] == 0:
            return {
                "status": "COMPLETE",
                "translated": progress["translated"],
                "skipped": progress["skipped"],
                "message": "All articles processed.",
            }
        else:
            # Shouldn't happen, but handle gracefully
            return {
                "status": "COMPLETE",
                "translated": progress["translated"],
                "skipped": progress["skipped"],
                "message": "No articles available.",
            }

    # Get progress
    progress = db.get_progress()

    return {
        "article": article,
        "progress": {
            "current": progress["in_progress"],
            "pending": progress["pending"],
            "translated": progress["translated"],
            "skipped": progress["skipped"],
        },
        "taxonomy": taxonomy.get_taxonomy_summary(),
        "workflow_reminder": WORKFLOW_REMINDER,
    }


def get_progress() -> dict[str, Any]:
    """
    Returns counts by processing_status.

    Response:
    {
        "progress": {
            "pending": 47,
            "in_progress": 1,
            "translated": 2,
            "skipped": 0,
            "total": 50
        },
        "session": {
            "articles_processed_count": 3,
            "human_review_interval": 5,
            "remaining_before_pause": 2
        }
    }
    """
    db = get_database()

    progress = db.get_progress()
    state = db.get_session_state()

    total = sum(progress.values())
    remaining = max(0, state["human_review_interval"] - state["articles_processed_count"])

    return {
        "progress": {
            "pending": progress["pending"],
            "in_progress": progress["in_progress"],
            "translated": progress["translated"],
            "skipped": progress["skipped"],
            "total": total,
        },
        "session": {
            "articles_processed_count": state["articles_processed_count"],
            "human_review_interval": state["human_review_interval"],
            "remaining_before_pause": remaining,
        },
    }


def set_human_review_interval(interval: int) -> dict[str, Any]:
    """
    Set how many articles to process before pausing.

    Range: 1-20. Default: 5.

    Response:
    {"success": true, "interval": 5}

    or

    {"success": false, "error": "Interval must be between 1 and 20."}
    """
    db = get_database()
    return db.set_human_review_interval(interval)


def reset_session_counter() -> dict[str, Any]:
    """
    Reset the session counter after human review.

    Response:
    {"success": true, "message": "Session counter reset."}
    """
    db = get_database()
    return db.reset_session_counter()


def skip_article(article_id: str, reason: str, flag_code: str) -> dict[str, Any]:
    """
    Marks article as skipped with reason.

    Per D9:
    - Sets processing_status = 'skipped'
    - Stores reason in processing_notes
    - Stores flag_code in processing_flags as JSON array
    - Does NOT increment session counter
    - Skipped articles can be reset to 'pending' via admin interface

    Response:
    {"success": true, "article_id": "post-id-16054"}
    """
    db = get_database()
    taxonomy = get_taxonomy()

    # Validate flag code
    if not taxonomy.is_valid_flag(flag_code):
        return {
            "success": False,
            "error": f"Invalid flag code: '{flag_code}'",
            "valid_flags": list(taxonomy.get_all_flag_codes()),
        }

    # Clear chunk cache for this article
    clear_chunk_cache(article_id)

    return db.mark_article_skipped(article_id, reason, flag_code)


# --- Phase 2: get_chunk() ---

def get_chunk(article_id: str, chunk_number: int) -> dict[str, Any]:
    """
    Get a chunk of article text for translation.

    Returns one chunk (3-5 paragraphs) of the article.
    First call triggers PDF fetch (from cache) and extraction.

    Per the plan's response schemas:

    SUCCESS (more chunks remain):
    {
        "chunk_number": 1,
        "total_chunks": 5,
        "text": "...",
        "glossary_terms": {"demand avoidance": "évitement des demandes"},
        "instruction": "Translate this chunk faithfully...",
        "extraction_warnings": [],
        "complete": false
    }

    SUCCESS (no more chunks):
    {
        "complete": true,
        "total_chunks": 5,
        "next_step": "Call validate_classification() with your classification decisions."
    }

    EXTRACTION FAILED (blocking — cannot proceed):
    {
        "error": true,
        "error_code": "EXTRACTION_FAILED",
        "problems": ["GARBLED", "TOOSHORT"],
        "action": "Call skip_article(article_id, 'PDF extraction failed: GARBLED', 'PDFEXTRACT')"
    }

    ARTICLE NOT FOUND:
    {
        "error": true,
        "error_code": "ARTICLE_NOT_FOUND",
        "action": "Call get_next_article() to get a valid article."
    }

    PAYWALLED (per D11 — should not call get_chunk on paywalled articles):
    {
        "error": true,
        "error_code": "PAYWALLED",
        "action": "This article is paywalled. Skip to validate_classification()."
    }
    """
    db = get_database()

    # Verify article exists
    article = db.get_article_by_id(article_id)
    if article is None:
        return {
            "error": True,
            "error_code": "ARTICLE_NOT_FOUND",
            "action": "Call get_next_article() to get a valid article.",
        }

    # Guard: paywalled articles should not call get_chunk (per D11)
    if not article.get("open_access"):
        return {
            "error": True,
            "error_code": "PAYWALLED",
            "action": "This article is paywalled (open_access=false). Skip to validate_classification() — only title and summary are translated for paywalled articles.",
        }

    # Check chunk cache first
    cache_entry = _get_cached_entry(article_id)

    if cache_entry is None:
        # First chunk request — trigger extraction
        logger.info(f"Extracting text for article {article_id}")

        # Get cached file path
        cached_path = get_cached_path(article_id)
        if cached_path is None:
            # No cached file — check if we have a source URL to fetch
            source_url = article.get("source_url")
            if not source_url:
                return {
                    "error": True,
                    "error_code": "NO_SOURCE",
                    "problems": ["NOURL"],
                    "action": f"Call skip_article('{article_id}', 'No source URL available', 'NOURL')",
                }

            # For Phase 2, we expect files to be pre-cached
            # URL fetching will be added in a later phase
            return {
                "error": True,
                "error_code": "NOT_CACHED",
                "problems": [],
                "action": f"Call skip_article('{article_id}', 'Source file not cached. Place PDF in cache/articles/{article_id}.pdf or wait for URL fetching (future phase).', 'PDFEXTRACT')",
            }

        # Extract text
        result = extract_article_text(cached_path)

        if not result.usable:
            return {
                "error": True,
                "error_code": "EXTRACTION_FAILED",
                "problems": result.problems,
                "action": f"Call skip_article('{article_id}', 'PDF extraction failed: {', '.join(result.problems)}', 'PDFEXTRACT')",
            }

        # Split into chunks and cache with metadata
        chunks = _split_into_chunks(result.text)
        _set_cached_entry(
            article_id,
            chunks,
            extractor_used=result.extractor_used,
            extraction_problems=result.problems,
        )

        logger.info(
            f"Article {article_id}: {len(chunks)} chunks, "
            f"extractor={result.extractor_used}, warnings={result.problems}"
        )

        cache_entry = _get_cached_entry(article_id)

    # Check if chunk_number is valid
    if chunk_number < 1:
        chunk_number = 1

    chunks = cache_entry.chunks

    if chunk_number > len(chunks):
        # No more chunks — article text complete
        # Include extraction metadata for save_article (Phase 4)
        return {
            "complete": True,
            "total_chunks": len(chunks),
            "extraction_metadata": {
                "extractor_used": cache_entry.extractor_used,
                "extraction_problems": cache_entry.extraction_problems,
            },
            "next_step": "Call validate_classification() with your classification decisions.",
        }

    # Get the requested chunk
    chunk_text = chunks[chunk_number - 1]

    # Find glossary terms in THIS chunk only
    glossary_terms = find_glossary_terms_in_text(chunk_text)

    # Include extraction warnings on every chunk (especially useful on chunk 1)
    # These are WARNING-level issues that don't block translation but should be flagged
    extraction_warnings = [
        p for p in cache_entry.extraction_problems
        if p not in ("UNUSABLE", "TOOSHORT", "GARBLED")  # Only non-blocking
    ]

    return {
        "chunk_number": chunk_number,
        "total_chunks": len(chunks),
        "text": chunk_text,
        "glossary_terms": glossary_terms,
        "instruction": CHUNK_INSTRUCTION,
        "extraction_warnings": extraction_warnings,
        "complete": False,
    }
