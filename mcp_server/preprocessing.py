"""
Preprocessing tools for the PDA Translation Machine.

Tools for PDF extraction, parsing, and enhancement before translation.
These tools guide Claude through:
1. Listing PDFs awaiting processing
2. Extracting PDFs via Datalab API
3. Parsing extracted JSON into article structure
4. Reviewing parsed articles with suggestions
5. Applying enhancements (manual + auto)
6. Submitting to database for human review
"""

import json
import logging
import os
import re
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

# Add scripts directory to path for imports
scripts_dir = Path(__file__).parent.parent / 'scripts'
sys.path.insert(0, str(scripts_dir))

from parse_article_blocks import parse_blocks, extract_text
from enhance_parsed_article import (
    detect_method, detect_peer_reviewed, detect_voice,
    load_article_type_mapping, extract_year_from_citation
)

from .taxonomy import get_taxonomy

logger = logging.getLogger(__name__)

# Directories
PROJECT_ROOT = Path(__file__).parent.parent
INTAKE_DIR = PROJECT_ROOT / "intake" / "articles"
PROCESSED_DIR = PROJECT_ROOT / "intake" / "processed"
CACHE_DIR = PROJECT_ROOT / "cache" / "articles"


def list_intake_pdfs() -> dict[str, Any]:
    """
    List PDFs in intake/articles/ awaiting processing.

    Returns PDFs that haven't been extracted yet, plus those already extracted.
    Cross-references with cache/articles/*.json to determine status.
    """
    INTAKE_DIR.mkdir(parents=True, exist_ok=True)
    PROCESSED_DIR.mkdir(parents=True, exist_ok=True)
    CACHE_DIR.mkdir(parents=True, exist_ok=True)

    # Get all PDFs in intake
    intake_pdfs = sorted(INTAKE_DIR.glob("*.pdf"))

    # Get already-extracted slugs (have .json in cache)
    extracted_slugs = {f.stem for f in CACHE_DIR.glob("*.json") if not f.stem.endswith('_parsed')}

    # Get processed PDFs count
    processed_pdfs = list(PROCESSED_DIR.glob("*.pdf"))

    available = []
    already_extracted = []

    for pdf in intake_pdfs:
        slug = slugify(pdf.stem)
        stat = pdf.stat()
        modified = datetime.fromtimestamp(stat.st_mtime).strftime('%Y-%m-%d')
        size_kb = stat.st_size // 1024

        if slug in extracted_slugs:
            already_extracted.append({
                "filename": pdf.name,
                "slug": slug,
                "size_kb": size_kb,
                "modified": modified
            })
        else:
            available.append({
                "filename": pdf.name,
                "slug": slug,
                "size_kb": size_kb,
                "modified": modified
            })

    return {
        "available": available,
        "already_extracted": already_extracted,
        "counts": {
            "available": len(available),
            "extracted": len(already_extracted),
            "processed": len(processed_pdfs)
        },
        "next_step": "Call extract_pdf('<filename>') to process a PDF, or parse_extracted_article('<slug>') for already-extracted files."
    }


def extract_pdf(filename: str) -> dict[str, Any]:
    """
    Submit PDF to Datalab Marker API and wait for completion.

    This is a blocking operation that typically takes 30-120 seconds.
    """
    pdf_path = INTAKE_DIR / filename

    if not pdf_path.exists():
        return {
            "success": False,
            "error": "FILE_NOT_FOUND",
            "details": f"File not found: {pdf_path}",
            "action": "Check filename. Use list_intake_pdfs() to see available files."
        }

    slug = slugify(pdf_path.stem)
    output_path = CACHE_DIR / f"{slug}.json"

    # Check if already extracted
    if output_path.exists():
        return {
            "success": False,
            "error": "ALREADY_EXTRACTED",
            "details": f"Already extracted: {slug}",
            "slug": slug,
            "action": f"Call parse_extracted_article('{slug}') to parse it."
        }

    # Import extraction functions from batch_extract
    try:
        from batch_extract import submit_pdf, poll_and_save
    except ImportError:
        return {
            "success": False,
            "error": "IMPORT_ERROR",
            "details": "Could not import batch_extract.py functions",
            "action": "Ensure scripts/batch_extract.py is available"
        }

    api_key = os.environ.get('DATALAB_API_KEY')
    if not api_key:
        return {
            "success": False,
            "error": "NO_API_KEY",
            "details": "DATALAB_API_KEY environment variable not set",
            "action": "Set DATALAB_API_KEY environment variable"
        }

    logger.info(f"Submitting PDF for extraction: {filename}")

    try:
        result = submit_pdf(pdf_path)
        request_id = result.get('request_id')

        if not request_id:
            return {
                "success": False,
                "error": "SUBMISSION_FAILED",
                "details": f"API response: {result}",
                "action": "Check API key and try again"
            }

        logger.info(f"Request ID: {request_id}, polling for completion...")
        success = poll_and_save(request_id, output_path)

        if not success:
            return {
                "success": False,
                "error": "EXTRACTION_FAILED",
                "details": "Polling timed out or extraction failed",
                "action": "Try again or check Datalab status"
            }

        # Read the result to get stats
        with open(output_path, 'r') as f:
            data = json.load(f)

        # Move PDF to processed folder
        PROCESSED_DIR.mkdir(parents=True, exist_ok=True)
        processed_path = PROCESSED_DIR / filename
        try:
            import shutil
            shutil.move(str(pdf_path), str(processed_path))
            logger.info(f"Moved {filename} to intake/processed/")
            moved = True
        except Exception as move_err:
            logger.warning(f"Could not move {filename} to processed: {move_err}")
            moved = False

        return {
            "success": True,
            "slug": slug,
            "json_path": str(output_path),
            "stats": {
                "blocks": len(data.get('blocks', [])),
                "pages": data.get('page_count', 0),
                "images": data.get('images_count', 0)
            },
            "moved_to_processed": moved,
            "next_step": f"Call parse_extracted_article('{slug}') to structure the content."
        }

    except Exception as e:
        logger.exception(f"Extraction failed for {filename}")
        return {
            "success": False,
            "error": "DATALAB_ERROR",
            "details": str(e),
            "action": "Check error details and retry"
        }


def parse_extracted_article(slug: str) -> dict[str, Any]:
    """
    Run mechanical parser on Datalab JSON, create _parsed.json.
    """
    json_path = CACHE_DIR / f"{slug}.json"

    if not json_path.exists():
        return {
            "success": False,
            "error": "NOT_EXTRACTED",
            "details": f"No Datalab JSON found for slug '{slug}'",
            "action": "Call extract_pdf() first, or check the slug is correct."
        }

    try:
        # Run the parser
        result = parse_blocks(json_path)

        # Save parsed result
        parsed_path = CACHE_DIR / f"{slug}_parsed.json"

        # Store body_html separately (can be large)
        body_html = result.get('body_html', '')
        output = {**result}
        output['body_html_chars'] = len(body_html)

        # Save full result including body_html for later use
        with open(parsed_path, 'w', encoding='utf-8') as f:
            # Save everything including body_html
            json.dump(result, f, ensure_ascii=False, indent=2)

        # Build summary for response
        summary = {
            "title": result.get('title'),
            "authors": result.get('authors'),
            "year": result.get('year'),
            "doi": result.get('doi'),
            "abstract_chars": len(result.get('abstract') or ''),
            "body_chars": len(body_html),
            "references_count": len(result.get('references', [])),
            "figures_count": len(result.get('figures', [])),
            "tables_count": len(result.get('tables', []))
        }

        return {
            "success": True,
            "slug": slug,
            "parsed_path": str(parsed_path),
            "summary": summary,
            "warnings": result.get('warnings', []),
            "next_step": f"Call get_article_for_review('{slug}') to review and enhance."
        }

    except Exception as e:
        logger.exception(f"Parsing failed for {slug}")
        return {
            "success": False,
            "error": "PARSE_ERROR",
            "details": str(e),
            "action": "Check the JSON file and error details"
        }


def get_article_for_review(slug: str) -> dict[str, Any]:
    """
    Get parsed article + suggestions + raw blocks sample for Claude to review.

    Returns parsed data, auto-detected suggestions (method, voice, peer_reviewed),
    and a sample of raw blocks from pages 0-1 so Claude can find missing info.
    """
    parsed_path = CACHE_DIR / f"{slug}_parsed.json"
    json_path = CACHE_DIR / f"{slug}.json"

    if not parsed_path.exists():
        if json_path.exists():
            return {
                "success": False,
                "error": "NOT_PARSED",
                "details": f"Extracted but not parsed: {slug}",
                "action": f"Call parse_extracted_article('{slug}') first."
            }
        return {
            "success": False,
            "error": "NOT_FOUND",
            "details": f"No article found for slug '{slug}'",
            "action": "Check the slug or use list_intake_pdfs() to see available files."
        }

    # Load parsed data
    with open(parsed_path, 'r', encoding='utf-8') as f:
        parsed = json.load(f)

    # Load raw blocks for sample
    with open(json_path, 'r', encoding='utf-8') as f:
        raw_data = json.load(f)

    # Build suggestions
    suggestions = {}
    mapping = load_article_type_mapping()

    # Method from article_type
    article_type = parsed.get('article_type')
    detected_method = detect_method(article_type, mapping)
    if detected_method:
        suggestions['method'] = {
            'value': detected_method,
            'reason': f"Mapped from article_type: '{article_type}'"
        }

    # Peer reviewed
    is_peer_reviewed, pr_reason = detect_peer_reviewed(parsed)
    suggestions['peer_reviewed'] = {
        'value': is_peer_reviewed,
        'reason': pr_reason
    }

    # Voice
    body_html = parsed.get('body_html', '')
    detected_voice, voice_reason = detect_voice(parsed, body_html)
    if detected_voice:
        suggestions['voice'] = {
            'value': detected_voice,
            'reason': voice_reason
        }

    # Year from citation if missing
    if not parsed.get('year') and parsed.get('citation'):
        detected_year = extract_year_from_citation(parsed.get('citation'))
        if detected_year:
            suggestions['year'] = {
                'value': detected_year,
                'reason': 'Extracted from citation'
            }

    # Identify missing fields
    missing_fields = []
    required = ['title', 'authors', 'abstract']
    for field in required:
        if not parsed.get(field):
            missing_fields.append(field)

    # Build raw blocks sample (first ~15 text blocks from pages 0-1)
    raw_blocks_sample = []
    blocks = raw_data.get('blocks', [])
    for block in blocks:
        page = block.get('page', 0)
        if page > 1:
            break

        block_type = block.get('block_type', '')

        # Skip images/figures in sample
        if block_type in ['Figure', 'Picture']:
            continue

        html = block.get('html', '')
        text = extract_text(html) if html else ''

        if text and len(text.strip()) > 5:
            raw_blocks_sample.append({
                'block_type': block_type,
                'text': text[:500],  # Truncate long blocks
                'page': page
            })

        if len(raw_blocks_sample) >= 15:
            break

    # Build parsed summary (without full body_html)
    parsed_summary = {
        'title': parsed.get('title'),
        'authors': parsed.get('authors'),
        'year': parsed.get('year'),
        'citation': parsed.get('citation'),
        'doi': parsed.get('doi'),
        'abstract': parsed.get('abstract'),
        'keywords': parsed.get('keywords'),
        'body_chars': len(body_html),
        'references_count': len(parsed.get('references', [])),
        'figures_count': len(parsed.get('figures', [])),
        'tables_count': len(parsed.get('tables', []))
    }

    return {
        "success": True,
        "slug": slug,
        "parsed": parsed_summary,
        "suggestions": suggestions,
        "missing_fields": missing_fields,
        "warnings": parsed.get('warnings', []),
        "raw_blocks_sample": raw_blocks_sample,
        "next_step": "Review parsed data. Call apply_enhancements() with corrections (e.g., authors='John Smith, Jane Doe')."
    }


def apply_enhancements(
    slug: str,
    authors: str | None = None,
    year: str | None = None,
    citation: str | None = None,
    title: str | None = None,
    abstract: str | None = None,
    keywords: str | None = None,
    method: str | None = None,
    voice: str | None = None,
    peer_reviewed: bool | None = None,
    apply_suggestions: bool = True
) -> dict[str, Any]:
    """
    Apply corrections to parsed article JSON.

    If apply_suggestions=True, auto-detected values are applied first,
    then explicit parameters override them.
    """
    parsed_path = CACHE_DIR / f"{slug}_parsed.json"

    if not parsed_path.exists():
        return {
            "success": False,
            "error": "NOT_FOUND",
            "details": f"No parsed article found for slug '{slug}'",
            "action": f"Call parse_extracted_article('{slug}') first."
        }

    # Load parsed data
    with open(parsed_path, 'r', encoding='utf-8') as f:
        data = json.load(f)

    # Validate taxonomy values
    taxonomy = get_taxonomy()

    if method and method not in taxonomy.methods:
        return {
            "success": False,
            "error": "INVALID_VALUE",
            "details": f"Invalid method '{method}'. Valid: {', '.join(taxonomy.methods)}",
            "action": "Fix the value and retry."
        }

    if voice and voice not in taxonomy.voices:
        return {
            "success": False,
            "error": "INVALID_VALUE",
            "details": f"Invalid voice '{voice}'. Valid: {', '.join(taxonomy.voices)}",
            "action": "Fix the value and retry."
        }

    changes_applied = []

    # Apply suggestions first if requested
    if apply_suggestions:
        # Get suggestions
        mapping = load_article_type_mapping()

        # Method from article_type
        if not data.get('method') and not method:
            article_type = data.get('article_type')
            detected_method = detect_method(article_type, mapping)
            if detected_method:
                data['method'] = detected_method
                changes_applied.append(f"method: {detected_method} (from suggestion)")

        # Peer reviewed
        if data.get('peer_reviewed') is None and peer_reviewed is None:
            is_peer_reviewed, _ = detect_peer_reviewed(data)
            data['peer_reviewed'] = is_peer_reviewed
            changes_applied.append(f"peer_reviewed: {is_peer_reviewed} (from suggestion)")

        # Voice
        if not data.get('voice') and not voice:
            body_html = data.get('body_html', '')
            detected_voice, _ = detect_voice(data, body_html)
            if detected_voice:
                data['voice'] = detected_voice
                changes_applied.append(f"voice: {detected_voice} (from suggestion)")

        # Year from citation
        if not data.get('year') and not year:
            detected_year = extract_year_from_citation(data.get('citation'))
            if detected_year:
                data['year'] = detected_year
                changes_applied.append(f"year: {detected_year} (from suggestion)")

    # Apply explicit overrides
    if authors is not None:
        data['authors'] = authors
        changes_applied.append(f"authors: {authors}")

    if year is not None:
        data['year'] = year
        changes_applied.append(f"year: {year}")

    if citation is not None:
        data['citation'] = citation
        changes_applied.append(f"citation: {citation[:50]}...")

    if title is not None:
        data['title'] = title
        changes_applied.append(f"title: {title[:50]}...")

    if abstract is not None:
        data['abstract'] = abstract
        changes_applied.append(f"abstract: {abstract[:50]}...")

    if keywords is not None:
        data['keywords'] = keywords
        changes_applied.append(f"keywords: {keywords}")

    if method is not None:
        data['method'] = method
        changes_applied.append(f"method: {method}")

    if voice is not None:
        data['voice'] = voice
        changes_applied.append(f"voice: {voice}")

    if peer_reviewed is not None:
        data['peer_reviewed'] = peer_reviewed
        changes_applied.append(f"peer_reviewed: {peer_reviewed}")

    # Save updated JSON
    with open(parsed_path, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

    # Check remaining warnings
    remaining_warnings = []
    if not data.get('title'):
        remaining_warnings.append('[MISSING] No title')
    if not data.get('authors'):
        remaining_warnings.append('[MISSING] No authors')
    if not data.get('abstract'):
        remaining_warnings.append('[MISSING] No abstract')
    if not data.get('method'):
        remaining_warnings.append('[MISSING] No method classification')
    if not data.get('voice'):
        remaining_warnings.append('[MISSING] No voice classification')
    if data.get('peer_reviewed') is None:
        remaining_warnings.append('[MISSING] peer_reviewed not set')

    # Build final state summary
    final_state = {
        'title': data.get('title'),
        'authors': data.get('authors'),
        'year': data.get('year'),
        'method': data.get('method'),
        'voice': data.get('voice'),
        'peer_reviewed': data.get('peer_reviewed')
    }

    return {
        "success": True,
        "slug": slug,
        "changes_applied": changes_applied,
        "final_state": final_state,
        "remaining_warnings": remaining_warnings,
        "next_step": f"Call submit_for_review('{slug}') to add to database." if not remaining_warnings else "Fix remaining warnings before submitting."
    }


def submit_for_review(slug: str) -> dict[str, Any]:
    """
    Create article record in database with status='preprocessing'.

    Requires all required fields to be present (title, authors, abstract, method, voice, peer_reviewed).
    """
    parsed_path = CACHE_DIR / f"{slug}_parsed.json"

    if not parsed_path.exists():
        return {
            "success": False,
            "error": "NOT_FOUND",
            "details": f"No parsed article found for slug '{slug}'",
            "action": f"Call parse_extracted_article('{slug}') first."
        }

    # Load parsed data
    with open(parsed_path, 'r', encoding='utf-8') as f:
        data = json.load(f)

    # Validate required fields
    missing = []
    for field in ['title', 'authors', 'abstract', 'method', 'voice']:
        if not data.get(field):
            missing.append(field)
    if data.get('peer_reviewed') is None:
        missing.append('peer_reviewed')

    if missing:
        return {
            "success": False,
            "error": "MISSING_REQUIRED",
            "details": f"Cannot submit: missing required fields: {', '.join(missing)}",
            "action": "Call apply_enhancements() to fill missing fields first."
        }

    # Generate article_id from title and authors
    article_id = generate_article_id(data.get('title', ''), data.get('authors', ''), data.get('year', ''))

    # Import database
    from .database import get_database
    db = get_database()

    try:
        # Create the database record
        result = db.create_preprocessing_article(
            article_id=article_id,
            source_title=data.get('title'),
            authors=data.get('authors'),
            abstract=data.get('abstract'),
            body_html=data.get('body_html', ''),
            doi=data.get('doi'),
            citation=data.get('citation'),
            year=data.get('year'),
            method=data.get('method'),
            voice=data.get('voice'),
            peer_reviewed=data.get('peer_reviewed'),
            references_json=json.dumps(data.get('references', [])) if data.get('references') else None,
        )

        # Check if database operation succeeded
        if not result.get('success'):
            return {
                "success": False,
                "error": result.get('error', 'DATABASE_ERROR'),
                "details": result.get('details', 'Unknown error'),
                "action": "Check error details. Article may already exist."
            }

        return {
            "success": True,
            "article_id": article_id,
            "slug": slug,
            "status": "preprocessing",
            "message": "Article submitted for human review.",
            "admin_url": f"/admin/review/{slug}",
            "next_step": "Human reviews at /admin/review. After approval, article becomes 'pending' for translation."
        }

    except Exception as e:
        logger.exception(f"Failed to create database record for {slug}")
        return {
            "success": False,
            "error": "DATABASE_ERROR",
            "details": str(e),
            "action": "Check error details and database connection."
        }


def get_preprocessing_status() -> dict[str, Any]:
    """
    Get overview of preprocessing pipeline status.
    """
    INTAKE_DIR.mkdir(parents=True, exist_ok=True)
    PROCESSED_DIR.mkdir(parents=True, exist_ok=True)
    CACHE_DIR.mkdir(parents=True, exist_ok=True)

    # Intake PDFs
    intake_pdfs = list(INTAKE_DIR.glob("*.pdf"))

    # Processed PDFs (already extracted)
    processed_pdfs = list(PROCESSED_DIR.glob("*.pdf"))

    # Extracted (have .json but not _parsed.json)
    extracted_jsons = {f.stem for f in CACHE_DIR.glob("*.json") if not f.stem.endswith('_parsed')}
    parsed_jsons = {f.stem.replace('_parsed', '') for f in CACHE_DIR.glob("*_parsed.json")}

    not_parsed = extracted_jsons - parsed_jsons

    # Database status
    from .database import get_database
    db = get_database()
    progress = db.get_progress()

    return {
        "intake": {
            "pending_pdfs": len(intake_pdfs),
            "processed_pdfs": len(processed_pdfs),
            "files": [p.name for p in intake_pdfs[:10]]  # First 10
        },
        "cache": {
            "extracted": len(extracted_jsons),
            "parsed": len(parsed_jsons),
            "not_parsed": list(not_parsed)[:10]  # First 10
        },
        "database": {
            "preprocessing": progress.get('preprocessing', 0),
            "pending": progress.get('pending', 0),
            "in_progress": progress.get('in_progress', 0),
            "translated": progress.get('translated', 0),
            "skipped": progress.get('skipped', 0)
        },
        "next_step": "Call list_intake_pdfs() to see available PDFs, or get_next_article() to start translating."
    }


# --- Helper functions ---

def slugify(text: str) -> str:
    """Convert text to a clean URL-safe slug."""
    text = text.lower()
    text = re.sub(r'[^a-z0-9]+', '-', text)
    text = text.strip('-')
    text = re.sub(r'-+', '-', text)
    return text


def generate_article_id(title: str, authors: str, year: str | None) -> str:
    """Generate article ID from title, first author, and year."""
    # Extract first author's last name
    first_author = ''
    if authors:
        # Handle various formats: "Smith, J." or "J. Smith" or "John Smith"
        first_part = authors.split(',')[0].split('&')[0].strip()
        # Get last word (likely last name)
        words = first_part.split()
        if words:
            # Skip initials (single letters with or without period)
            last_name_words = [w for w in words if len(w.replace('.', '')) > 1]
            if last_name_words:
                # Slugify the author name to handle apostrophes etc.
                first_author = slugify(last_name_words[-1])

    # Get first few words of title
    title_slug = slugify(title)[:50] if title else 'untitled'

    # Combine
    parts = []
    if first_author:
        parts.append(first_author)
    if year:
        parts.append(year)
    parts.append(title_slug)

    return '-'.join(parts)
