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

Phase 4 tools:
- validate_classification() — validate classification fields, return token
- save_article() — save article with quality checks

Later phases will add:
- ingest_article()
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta
from typing import Any

from .database import get_database
from .taxonomy import get_taxonomy
from .glossary import find_glossary_terms_in_text, get_glossary_version, verify_glossary_terms
from .pdf_extraction import (
    extract_article_text,
    extract_pdf_metadata,
    get_cached_path,
    fetch_and_cache,
    CACHE_DIR,
    extract_pymupdf,
)
from pathlib import Path
import shutil
from .quality_checks import run_quality_checks
from .utils import slugify

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

            # Try to fetch from source URL
            logger.info(f"Fetching {article_id} from {source_url}")
            fetch_result = fetch_and_cache(article_id, source_url)

            if not fetch_result.success:
                # Map fetch errors to appropriate skip actions
                error_code = fetch_result.error_code
                if error_code == "PAYWALL":
                    return {
                        "error": True,
                        "error_code": "PAYWALL",
                        "problems": ["PAYWALL"],
                        "action": f"Call skip_article('{article_id}', '{fetch_result.error_message}', 'PAYWALL')",
                    }
                elif error_code == "NOT_FOUND":
                    return {
                        "error": True,
                        "error_code": "NOT_FOUND",
                        "problems": ["404"],
                        "action": f"Call skip_article('{article_id}', '{fetch_result.error_message}', '404')",
                    }
                else:
                    return {
                        "error": True,
                        "error_code": "FETCH_FAILED",
                        "problems": ["PDFEXTRACT"],
                        "action": f"Call skip_article('{article_id}', '{fetch_result.error_message}', 'PDFEXTRACT')",
                    }

            cached_path = fetch_result.path

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


# --- Phase 4: validate_classification() and save_article() ---

def validate_classification(
    article_id: str,
    method: str,
    voice: str,
    peer_reviewed: bool,
    open_access: bool,
    primary_category: str,
    secondary_categories: list[str],
    keywords: list[str]
) -> dict[str, Any]:
    """
    Validate classification fields against taxonomy.yaml.

    MUST be called before save_article() — returns token required for save.

    Per D17 and D20:
    - method must be one of: empirical, synthesis, theoretical, lived_experience
    - voice must be one of: academic, practitioner, organization, individual
    - primary_category: required, must exist in taxonomy.yaml
    - secondary_categories: 0-2 items, all must exist, no duplicates with primary
    - keywords: 5-15 entries

    Response schemas:

    SUCCESS:
    {
        "valid": true,
        "token": "abc123...",
        "next_step": "Call save_article() with this token within 30 minutes."
    }

    FAILURE:
    {
        "valid": false,
        "errors": ["Invalid method: 'empiric' — did you mean 'empirical'?", ...],
        "action": "Fix the errors and call validate_classification() again."
    }
    """
    db = get_database()
    taxonomy = get_taxonomy()

    errors: list[str] = []

    # Validate method
    if not taxonomy.is_valid_method(method):
        suggestion = _suggest_correction(method, taxonomy.methods)
        errors.append(f"Invalid method: '{method}'{suggestion}")

    # Validate voice
    if not taxonomy.is_valid_voice(voice):
        suggestion = _suggest_correction(voice, taxonomy.voices)
        errors.append(f"Invalid voice: '{voice}'{suggestion}")

    # Validate primary_category
    if not primary_category:
        errors.append("primary_category is required.")
    elif not taxonomy.is_valid_category(primary_category):
        suggestion = _suggest_correction(primary_category, taxonomy.categories)
        errors.append(f"Invalid primary_category: '{primary_category}'{suggestion}")

    # Validate secondary_categories
    if secondary_categories:
        if len(secondary_categories) > 2:
            errors.append(
                f"Too many secondary_categories: {len(secondary_categories)} provided, max 2 allowed."
            )

        for cat in secondary_categories:
            if not taxonomy.is_valid_category(cat):
                suggestion = _suggest_correction(cat, taxonomy.categories)
                errors.append(f"Invalid secondary_category: '{cat}'{suggestion}")

        # Check for duplicates between primary and secondary
        if primary_category in secondary_categories:
            errors.append(
                f"Duplicate category: '{primary_category}' appears in both primary and secondary."
            )

        # Check for duplicates within secondary
        if len(secondary_categories) != len(set(secondary_categories)):
            errors.append("Duplicate categories in secondary_categories.")

    # Validate keywords
    if not keywords:
        errors.append("keywords is required (5-15 items).")
    elif len(keywords) < 5:
        errors.append(f"Too few keywords: {len(keywords)} provided, minimum 5 required.")
    elif len(keywords) > 15:
        errors.append(f"Too many keywords: {len(keywords)} provided, maximum 15 allowed.")

    # If errors, return failure
    if errors:
        return {
            "valid": False,
            "errors": errors,
            "action": "Fix the errors and call validate_classification() again.",
        }

    # Store classification data and create token
    classification_data = {
        "method": method,
        "voice": voice,
        "peer_reviewed": peer_reviewed,
        "open_access": open_access,
        "primary_category": primary_category,
        "secondary_categories": secondary_categories,
        "keywords": keywords,
    }

    token = db.create_validation_token(article_id, classification_data)

    return {
        "valid": True,
        "token": token,
        "next_step": "Call save_article() with this token within 30 minutes.",
    }


def _suggest_correction(value: str, valid_options: list[str]) -> str:
    """
    Suggest a correction for an invalid value.

    Uses simple string matching to find similar valid options.
    """
    value_lower = value.lower()

    # Check for prefix match
    for opt in valid_options:
        if opt.lower().startswith(value_lower) or value_lower.startswith(opt.lower()):
            return f" — did you mean '{opt}'?"

    # Check for substring match
    for opt in valid_options:
        if value_lower in opt.lower() or opt.lower() in value_lower:
            return f" — did you mean '{opt}'?"

    # No close match found — list valid options
    return f". Valid options: {', '.join(valid_options)}"


def save_article(
    article_id: str,
    validation_token: str,
    source: str,
    doi: str | None,
    translated_title: str,
    translated_summary: str,
    translated_full_text: str | None,
    flags: list[dict[str, str]]
) -> dict[str, Any]:
    """
    Save completed article to database in single transaction.

    Per D8, D17, D22, and Part 4.1:
    - Validates token from validate_classification() — rejects if invalid/expired
    - Tokens are single-use and expire after 30 minutes
    - Runs quality checks (sentence count, word ratio, glossary recall)
    - BLOCKING flags reject the save; WARNING flags allow save with human review

    Flag handling (per D22):
    - flags: list of {"code": str, "detail": str}
    - Server validates each code against taxonomy.yaml
    - Server stores processing_flags = JSON array of codes
    - Server stores processing_notes = formatted string from details

    Response schemas:

    SUCCESS:
    {
        "success": true,
        "warning_flags": ["TERMMIS"],  # Empty if none
        "next_step": "Call get_next_article() to continue, or stop if SESSION_PAUSE."
    }

    BLOCKING FLAGS (save rejected):
    {
        "success": false,
        "blocking_flags": ["SENTMIS"],
        "details": {"SENTMIS": "Source: 45 sentences, Target: 32 sentences (ratio: 0.71)"},
        "action": "Fix the translation to address the blocking issue, then re-validate and save."
    }

    INVALID TOKEN:
    {
        "success": false,
        "error": "INVALID_TOKEN",
        "message": "Token not found.",
        "action": "Call validate_classification() again to get a fresh token."
    }
    """
    db = get_database()
    taxonomy = get_taxonomy()

    # Validate token
    token_result = db.validate_token(validation_token, article_id)
    if not token_result.get("valid"):
        return {
            "success": False,
            "error": token_result.get("error", "INVALID_TOKEN"),
            "message": token_result.get("message", "Token validation failed."),
            "action": "Call validate_classification() again to get a fresh token.",
        }

    # Extract classification data from token
    classification = token_result["classification_data"]

    # Validate flags format
    for flag in flags:
        if not isinstance(flag, dict):
            return {
                "success": False,
                "error": "INVALID_FLAGS",
                "message": "Each flag must be a dict with 'code' and 'detail' keys.",
                "action": "Fix flag format and retry.",
            }
        if "code" not in flag or "detail" not in flag:
            return {
                "success": False,
                "error": "INVALID_FLAGS",
                "message": "Each flag must have 'code' and 'detail' keys.",
                "action": "Fix flag format and retry.",
            }
        if not taxonomy.is_valid_flag(flag["code"]):
            return {
                "success": False,
                "error": "INVALID_FLAGS",
                "message": f"Invalid flag code: '{flag['code']}'",
                "valid_flags": list(taxonomy.get_all_flag_codes()),
                "action": "Fix flag code and retry.",
            }

    # Get article to retrieve source text for quality checks
    article = db.get_article_by_id(article_id)
    if not article:
        return {
            "success": False,
            "error": "ARTICLE_NOT_FOUND",
            "message": f"Article '{article_id}' not found.",
            "action": "Call get_next_article() to get a valid article.",
        }

    # Run quality checks if we have full text translation
    blocking_flags: dict[str, str] = {}
    warning_flags: list[str] = []

    if translated_full_text and classification.get("open_access"):
        # Get source text from cache for comparison
        cache_entry = _get_cached_entry(article_id)
        if cache_entry:
            # Join all chunks to get full source text
            source_text = "\n\n".join(cache_entry.chunks)

            # Get glossary terms found in source
            glossary_terms = find_glossary_terms_in_text(source_text)

            # Run glossary verification
            missing_terms = verify_glossary_terms(source_text, translated_full_text)

            # Run quality checks
            quality_results = run_quality_checks(
                source_en=source_text,
                translation_fr=translated_full_text,
                glossary_terms=glossary_terms,
                glossary_missing=missing_terms,
            )

            # Check for blocking flags
            if quality_results.sentence_check and quality_results.sentence_check.flag:
                sc = quality_results.sentence_check
                blocking_flags["SENTMIS"] = (
                    f"Source: {sc.source_count} sentences, "
                    f"Target: {sc.target_count} sentences (ratio: {sc.ratio})"
                )

            if quality_results.word_ratio_check and quality_results.word_ratio_check.flag:
                wc = quality_results.word_ratio_check
                blocking_flags["WORDMIS"] = (
                    f"Source: {wc.source_words} words, "
                    f"Target: {wc.target_words} words (ratio: {wc.ratio})"
                )

            # Collect warning flags
            warning_flags.extend(quality_results.warning_flags)

    # If blocking flags, reject save
    if blocking_flags:
        return {
            "success": False,
            "blocking_flags": list(blocking_flags.keys()),
            "details": blocking_flags,
            "action": "Fix the translation to address the blocking issue, then re-validate and save.",
        }

    # Prepare flag data for storage
    all_flag_codes = [f["code"] for f in flags]
    all_flag_codes.extend(warning_flags)

    # Format processing_notes from flag details
    notes_parts = []
    for flag in flags:
        if flag["detail"]:
            notes_parts.append(f"[{flag['code']}] {flag['detail']}")
    processing_notes = "; ".join(notes_parts)

    # Add warning flag notes
    if warning_flags:
        warning_note = f"[QUALITY] Auto-detected: {', '.join(warning_flags)}"
        if processing_notes:
            processing_notes = f"{processing_notes}; {warning_note}"
        else:
            processing_notes = warning_note

    # Get extraction metadata if available
    cache_entry = _get_cached_entry(article_id)
    extraction_method = cache_entry.extractor_used if cache_entry else None
    extraction_problems = cache_entry.extraction_problems if cache_entry else []

    # Get glossary version
    glossary_version = get_glossary_version()

    # Execute save in transaction
    # All operations use auto_commit=False to stay in one transaction
    try:
        # Update article with classification and status
        db.mark_article_translated(
            article_id=article_id,
            method=classification["method"],
            voice=classification["voice"],
            peer_reviewed=classification["peer_reviewed"],
            source=source,
            processing_flags=all_flag_codes,
            processing_notes=processing_notes,
            extraction_method=extraction_method,
            extraction_problems=extraction_problems,
            glossary_version=glossary_version,
        )

        # Save translation
        db.save_translation(
            article_id=article_id,
            target_language="fr",
            translated_title=translated_title,
            translated_summary=translated_summary,
            translated_full_text=translated_full_text,
        )

        # Set categories
        db.set_article_categories(
            article_id=article_id,
            primary_category=classification["primary_category"],
            secondary_categories=classification["secondary_categories"],
        )

        # Set keywords
        db.set_article_keywords(
            article_id=article_id,
            keywords=classification["keywords"],
        )

        # Mark token as used (within transaction)
        db.mark_token_used(validation_token, auto_commit=False)

        # Increment session counter (within transaction)
        db.increment_session_count(auto_commit=False)

        # Single commit for entire transaction
        db.commit()

        # Clear chunk cache for this article (after successful commit)
        clear_chunk_cache(article_id)

        return {
            "success": True,
            "warning_flags": warning_flags,
            "next_step": "Call get_next_article() to continue, or stop if SESSION_PAUSE.",
        }

    except Exception as e:
        # Rollback on any failure — everything reverts including token and counter
        db.rollback()
        logger.error(f"Save failed for article {article_id}: {e}")
        return {
            "success": False,
            "error": "SAVE_FAILED",
            "message": str(e),
            "action": "Retry save_article() or contact support if error persists.",
        }


# --- Phase 6: Article Ingestion (per D21) ---

# Paths for intake folder
PROJECT_ROOT = Path(__file__).parent.parent
INTAKE_DIR = PROJECT_ROOT / "intake" / "articles"


def _extract_summary_from_text(text: str, max_words: int = 150) -> str | None:
    """
    Extract a summary from the beginning of article text.

    Assumes the lede is not buried — takes content from the start
    after skipping obvious header material.

    Returns ~150 words from the beginning of the substantive content.
    """
    if not text or len(text.strip()) < 100:
        return None

    # Split into paragraphs
    paragraphs = [p.strip() for p in text.split('\n\n') if p.strip()]

    if not paragraphs:
        return None

    # Skip header-like paragraphs (very short, all caps, metadata)
    content_paragraphs = []
    for para in paragraphs:
        # Skip very short paragraphs (likely headers)
        if len(para.split()) < 10:
            continue
        # Skip paragraphs that look like metadata
        if any(marker in para.lower()[:50] for marker in ['doi:', 'issn:', '©', 'copyright', 'abstract']):
            continue
        # Skip all-caps paragraphs
        if para.isupper():
            continue
        content_paragraphs.append(para)
        # Collect enough for summary
        total_words = sum(len(p.split()) for p in content_paragraphs)
        if total_words >= max_words * 1.5:
            break

    if not content_paragraphs:
        # Fallback: just use first paragraphs
        content_paragraphs = paragraphs[:3]

    # Join and truncate to max_words
    combined = ' '.join(content_paragraphs)
    words = combined.split()
    if len(words) > max_words:
        # Find a sentence boundary near max_words
        truncated = ' '.join(words[:max_words])
        # Try to end at a sentence
        last_period = truncated.rfind('.')
        if last_period > len(truncated) * 0.6:  # At least 60% of the way through
            truncated = truncated[:last_period + 1]
        return truncated
    return combined


# _slugify is now imported from utils.py as slugify
_slugify = slugify


def _generate_article_id(title: str, author: str | None, doi: str | None) -> str:
    """
    Generate article ID from metadata.

    Format: {first-author-surname}-{year}-{title-words}
    Falls back to title-only if no author.
    """
    db = get_database()
    parts = []

    # Try to extract first author surname
    if author:
        # Handle "Smith, J." or "Smith, John" or "John Smith"
        first_author = author.split(",")[0].split(";")[0].strip()
        # If it has a space, take the last word (surname usually last in "First Last")
        if " " in first_author:
            first_author = first_author.split()[-1]
        parts.append(_slugify(first_author))

    # Try to extract year from title or DOI
    year_match = re.search(r'\b(19|20)\d{2}\b', title or "")
    if year_match:
        parts.append(year_match.group())

    # Add title words (first 5 significant words)
    if title:
        title_words = _slugify(title).split("-")
        # Skip common words
        skip_words = {"the", "a", "an", "of", "in", "on", "for", "and", "to", "with"}
        significant = [w for w in title_words if w and w not in skip_words][:5]
        parts.extend(significant)

    base_id = "-".join(parts) or "untitled-article"

    # Check for duplicates and append suffix if needed
    candidate_id = base_id
    counter = 2
    while db.article_exists(candidate_id):
        candidate_id = f"{base_id}-{counter}"
        counter += 1

    return candidate_id


def ingest_article(filename: str) -> dict[str, Any]:
    """
    Ingest a PDF from intake/articles/ into the database.

    Creates article with status 'pending' — ready for translation immediately.
    Source URL can be added later via set_article_url() or during save.

    WORKFLOW:
    1. Verify file exists in intake/articles/
    2. Extract PDF metadata (title, authors, DOI)
    3. Extract text and generate summary from first ~150 words
    4. Generate article_id from metadata
    5. If DOI found, construct suggested URL (for convenience)
    6. Create article record with status 'pending'
    7. Copy PDF to cache/articles/{article_id}.pdf
    8. Return article details — ready for get_next_article()

    Args:
        filename: Name of PDF in intake/articles/ (e.g., "smith-2024-pda.pdf")

    Returns:
        Success dict with article details, or error dict.
    """
    db = get_database()

    # 1. Verify file exists
    source_path = INTAKE_DIR / filename
    if not source_path.exists():
        return {
            "success": False,
            "error": "FILE_NOT_FOUND",
            "details": f"File not found: {source_path}",
        }

    if not filename.lower().endswith(".pdf"):
        return {
            "success": False,
            "error": "INVALID_FILE",
            "details": "Only PDF files are supported.",
        }

    # 2. Extract PDF metadata
    try:
        metadata = extract_pdf_metadata(source_path)
    except Exception as e:
        logger.error(f"Failed to extract metadata from {filename}: {e}")
        return {
            "success": False,
            "error": "EXTRACTION_FAILED",
            "details": f"Could not extract metadata: {e}",
        }

    title = metadata.get("title") or filename.replace(".pdf", "").replace("-", " ").replace("_", " ")
    author = metadata.get("author")
    doi = metadata.get("doi")
    source = metadata.get("creator")  # Often contains journal name

    # 3. Extract text and generate summary
    summary_original = None
    try:
        # Use PyMuPDF for quick extraction (we'll do full extraction later during translation)
        text = extract_pymupdf(source_path)
        if text:
            summary_original = _extract_summary_from_text(text, max_words=150)
            if summary_original:
                logger.info(f"Generated summary ({len(summary_original.split())} words) for {filename}")
    except Exception as e:
        logger.warning(f"Could not generate summary for {filename}: {e}")
        # Not fatal — continue without summary

    # 4. Generate article ID
    article_id = _generate_article_id(title, author, doi)

    # 5. Construct URL from DOI if available (for convenience, not required)
    suggested_url = None
    if doi:
        clean_doi = doi.rstrip(".")
        suggested_url = f"https://doi.org/{clean_doi}"

    # 6. Create article record — directly in 'pending' status
    try:
        db.create_article(
            article_id=article_id,
            source_title=title,
            source_url=suggested_url,  # Use DOI URL if available, can be updated later
            summary_original=summary_original,
            doi=doi,
            source=source,
            open_access=True,  # We have the PDF
            processing_status="pending",  # Ready for translation immediately
        )
    except Exception as e:
        logger.error(f"Failed to create article record: {e}")
        return {
            "success": False,
            "error": "DATABASE_ERROR",
            "details": str(e),
        }

    # 7. Copy PDF to cache
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    cache_path = CACHE_DIR / f"{article_id}.pdf"
    try:
        shutil.copy(source_path, cache_path)
        logger.info(f"Cached PDF: {cache_path}")
    except Exception as e:
        logger.error(f"Failed to copy PDF to cache: {e}")
        db.execute("DELETE FROM articles WHERE id = ?", (article_id,))
        db.commit()
        return {
            "success": False,
            "error": "CACHE_FAILED",
            "details": str(e),
        }

    # 8. Return success — article is ready for translation
    return {
        "success": True,
        "article": {
            "id": article_id,
            "source_title": title,
            "authors": author,
            "source": source,
            "doi": doi,
            "source_url": suggested_url,
            "summary_preview": summary_original[:200] + "..." if summary_original and len(summary_original) > 200 else summary_original,
            "open_access": True,
        },
        "next_step": f"Article '{article_id}' added to queue. Call get_next_article() to begin translation.",
    }


def search_article_url(article_id: str) -> dict[str, Any]:
    """
    Search the web for the canonical URL of an article.

    Uses the article's title and authors to search for the original source.
    Returns candidate URLs for the user to verify.

    This is meant to be called by Claude, who will then use web search
    to find the canonical URL and report back.

    Args:
        article_id: The article ID to search for

    Returns:
        Article details needed for searching, or error if not found.
    """
    db = get_database()

    article = db.get_article_by_id(article_id)
    if not article:
        return {
            "success": False,
            "error": "NOT_FOUND",
            "details": f"Article '{article_id}' not found.",
        }

    # If URL already exists, inform the caller
    if article.get("source_url"):
        return {
            "success": True,
            "has_url": True,
            "current_url": article["source_url"],
            "message": "Article already has a source URL. Use set_article_url() to update if needed.",
        }

    # Return search hints
    title = article.get("source_title", "")
    doi = article.get("doi")
    source = article.get("source")

    # Build search query suggestion
    search_terms = [title]
    if source:
        search_terms.append(source)

    return {
        "success": True,
        "has_url": False,
        "article_id": article_id,
        "title": title,
        "doi": doi,
        "source": source,
        "search_query": " ".join(search_terms[:2]),  # Title + source usually enough
        "instructions": (
            "Search the web for this article to find its canonical URL. "
            "Look for the original publication on the journal/publisher website, "
            "or on repositories like PubMed, Google Scholar, or ResearchGate. "
            "Once found, call set_article_url(article_id, url) to save it."
        ),
    }


def set_article_url(article_id: str, source_url: str) -> dict[str, Any]:
    """
    Set or update the source URL for an article.

    Can be called at any time — before, during, or after translation.
    URL is required before publishing but not for translation.

    Args:
        article_id: The article ID
        source_url: The canonical URL where the original can be found

    Returns:
        Success dict or error dict with details.
    """
    db = get_database()

    article = db.get_article_by_id(article_id)
    if not article:
        return {
            "success": False,
            "error": "NOT_FOUND",
            "details": f"Article '{article_id}' not found.",
        }

    # Basic URL validation
    if not source_url or not source_url.startswith(("http://", "https://")):
        return {
            "success": False,
            "error": "INVALID_URL",
            "details": "URL must start with http:// or https://",
        }

    db.execute(
        "UPDATE articles SET source_url = ? WHERE id = ?",
        (source_url, article_id)
    )
    db.commit()

    return {
        "success": True,
        "article_id": article_id,
        "source_url": source_url,
        "message": "Source URL updated.",
    }
