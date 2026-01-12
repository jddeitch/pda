"""
MCP Server entry point for the Translation Machine.

This server provides tools for the translation pipeline:
- get_next_article() — get next article to translate
- get_progress() — get translation progress statistics
- get_chunk() — get a chunk of article text for translation (Phase 2)
- validate_classification() — validate article classification (Phase 4)
- save_article() — save translated article (Phase 4)
- skip_article() — skip an article with reason
- set_human_review_interval() — configure review interval
- reset_session_counter() — reset after human review
- ingest_article() — add new article from intake/ folder (Phase 6)
- set_article_url() — set/update source URL for an article (Phase 6)

Usage:
    python -m mcp_server.server

Or run via the entry point:
    pda-mcp
"""

import json
import logging
from typing import Any

from mcp.server.fastmcp import FastMCP

from .database import get_database
from .taxonomy import get_taxonomy
from . import tools
from . import preprocessing


# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)


# Create the MCP server
mcp = FastMCP("PDA Translation Machine")


# --- Tool: get_next_article ---

@mcp.tool()
def get_next_article() -> dict[str, Any]:
    """
    Get the next article to translate.

    Returns the next article needing work, plus fresh taxonomy data.
    Prioritizes in_progress (crash recovery) over pending.
    Checks human_review_interval — returns SESSION_PAUSE if limit reached.

    Response contains:
    - article: Article metadata (id, source_title, source_url, summary_original, open_access, doi)
    - progress: Counts by status (pending, in_progress, translated, skipped)
    - taxonomy: Valid values for method, voice, categories
    - workflow_reminder: Steps to follow

    Or returns SESSION_PAUSE / COMPLETE status if applicable.
    """
    return tools.get_next_article()


# --- Tool: get_progress ---

@mcp.tool()
def get_progress() -> dict[str, Any]:
    """
    Get translation progress statistics.

    Returns counts by processing_status and session state.

    Response contains:
    - progress: Counts (pending, in_progress, translated, skipped, total)
    - session: Current session state (articles_processed_count, human_review_interval, remaining_before_pause)
    """
    return tools.get_progress()


# --- Tool: skip_article ---

@mcp.tool()
def skip_article(article_id: str, reason: str, flag_code: str) -> dict[str, Any]:
    """
    Skip an article with a reason and flag code.

    Use this when:
    - PDF extraction fails (flag_code: PDFEXTRACT)
    - Article is paywalled with no summary (flag_code: PAYWALL)
    - Article is not relevant (flag_code: SKIP)
    - Quality issues prevent translation (flag_code: QUALITY)

    Does NOT increment session counter — skips don't count toward review interval.

    Args:
        article_id: The article ID to skip
        reason: Human-readable explanation
        flag_code: Flag code from taxonomy.yaml processing_flags

    Returns:
        {"success": true, "article_id": "..."}
    """
    return tools.skip_article(article_id, reason, flag_code)


# --- Tool: get_chunk (Phase 2) ---

@mcp.tool()
def get_chunk(article_id: str, chunk_number: int) -> dict[str, Any]:
    """
    Get a chunk of article text for translation.

    Returns one chunk (3-5 paragraphs) of the article.
    First call triggers PDF extraction and caching.

    WORKFLOW FOR EACH CHUNK:
    1. Read the instruction field — it contains translation rules
    2. Translate the chunk faithfully using provided glossary terms
    3. Append translation to your running translated_chunks list
    4. Note any classification signals (method, voice, peer_reviewed)
    5. Note any flags (TBL if tables, FIG if figures, AMBIG if unclear)
    6. Call get_chunk(article_id, chunk_number + 1)
    7. Repeat until response contains "complete": true

    Args:
        article_id: The article ID from get_next_article()
        chunk_number: Which chunk to retrieve (1-indexed)

    Returns on success (more chunks):
        chunk_number, total_chunks, text, glossary_terms, instruction, complete=false

    Returns on success (no more chunks):
        complete=true, total_chunks, next_step

    Returns on error:
        error=true, error_code, problems, action
    """
    return tools.get_chunk(article_id, chunk_number)


# --- Tool: set_human_review_interval ---

@mcp.tool()
def set_human_review_interval(interval: int) -> dict[str, Any]:
    """
    Set how many articles to process before pausing for human review.

    Range: 1-20. Default: 5.

    Recommended ramp-up:
    - First 5 articles: interval = 1 (approve each one)
    - Next 10 articles: interval = 3
    - Steady state: interval = 5-10

    Args:
        interval: Number of articles before SESSION_PAUSE (1-20)

    Returns:
        {"success": true, "interval": 5}
    """
    return tools.set_human_review_interval(interval)


# --- Tool: reset_session_counter ---

@mcp.tool()
def reset_session_counter() -> dict[str, Any]:
    """
    Reset the session counter after human review.

    Call this after reviewing articles in /admin to continue processing.
    Counter also auto-resets at local midnight.

    Returns:
        {"success": true, "message": "Session counter reset."}
    """
    return tools.reset_session_counter()


# --- Tool: validate_classification (Phase 4) ---

@mcp.tool()
def validate_classification(
    article_id: str,
    method: str,
    voice: str,
    peer_reviewed: bool,
    source: str,
    primary_category: str,
    secondary_categories: list[str],
    keywords: list[str],
) -> dict[str, Any]:
    """
    Validate article classification and get a validation token.

    Call this after translating all chunks. Returns a token needed for save_article().

    Args:
        article_id: The article ID
        method: One of: empirical, synthesis, theoretical, lived_experience
        voice: One of: academic, practitioner, organization, individual
        peer_reviewed: True if peer-reviewed
        source: Journal/institution name
        primary_category: Main category ID
        secondary_categories: Additional category IDs
        keywords: 3-7 keywords for search

    Returns on success:
        {"valid": true, "validation_token": "...", "next_step": "Call save_article()..."}

    Returns on failure:
        {"valid": false, "errors": [...], "action": "Fix errors and retry"}
    """
    return tools.validate_classification(
        article_id=article_id,
        method=method,
        voice=voice,
        peer_reviewed=peer_reviewed,
        source=source,
        primary_category=primary_category,
        secondary_categories=secondary_categories,
        keywords=keywords,
    )


# --- Tool: save_article (Phase 4) ---

@mcp.tool()
def save_article(
    article_id: str,
    validation_token: str,
    translated_title: str,
    translated_summary: str,
    translated_full_text: str | None,
    flags: list[dict[str, str]],
) -> dict[str, Any]:
    """
    Save translated article with quality checks.

    Requires a validation_token from validate_classification().
    Runs quality checks and saves if passing.

    Args:
        article_id: The article ID
        validation_token: Token from validate_classification()
        translated_title: French title
        translated_summary: French summary
        translated_full_text: French full text (or null if summary-only)
        flags: List of {"code": "...", "detail": "..."} for any issues

    Returns on success:
        {"success": true, "warning_flags": [...], "next_step": "..."}

    Returns on quality failure:
        {"success": false, "error": "QUALITY_CHECK_FAILED", "blocking_flags": [...], "action": "..."}
    """
    return tools.save_article(
        article_id=article_id,
        validation_token=validation_token,
        translated_title=translated_title,
        translated_summary=translated_summary,
        translated_full_text=translated_full_text,
        flags=flags,
    )


# --- Tool: ingest_article (Phase 6) ---

@mcp.tool()
def ingest_article(filename: str) -> dict[str, Any]:
    """
    Ingest a PDF from intake/articles/ into the database.

    Creates article with status 'pending' — ready for translation immediately.
    If DOI is found, source_url is auto-populated from doi.org.

    Args:
        filename: Name of PDF in intake/articles/ (e.g., "smith-2024-pda.pdf")

    Returns on success:
        {
            "success": true,
            "article": {"id": "...", "source_title": "...", "doi": "...", "source_url": "..."},
            "next_step": "Article added to queue. Call get_next_article() to begin."
        }

    Returns on failure:
        {"success": false, "error": "FILE_NOT_FOUND|EXTRACTION_FAILED|DUPLICATE", "details": "..."}
    """
    return tools.ingest_article(filename)


# --- Tool: search_article_url (Phase 6) ---

@mcp.tool()
def search_article_url(article_id: str) -> dict[str, Any]:
    """
    Get article details to search for its canonical URL.

    Use this for articles without a source_url. Returns the title and search hints.
    After finding the URL via web search, call set_article_url() to save it.

    Args:
        article_id: The article ID

    Returns:
        Article details for searching, or indicates URL already exists.
    """
    return tools.search_article_url(article_id)


# --- Tool: set_article_url (Phase 6) ---

@mcp.tool()
def set_article_url(article_id: str, source_url: str) -> dict[str, Any]:
    """
    Set or update the source URL for an article.

    Can be called at any time — before, during, or after translation.
    URL is needed before publishing but not required for translation.

    Args:
        article_id: The article ID
        source_url: The canonical URL where the original can be found

    Returns on success:
        {"success": true, "article_id": "...", "source_url": "...", "message": "Source URL updated."}

    Returns on failure:
        {"success": false, "error": "NOT_FOUND|INVALID_URL", "details": "..."}
    """
    return tools.set_article_url(article_id, source_url)


# --- Preprocessing Tools ---

@mcp.tool()
def list_intake_pdfs() -> dict[str, Any]:
    """
    List PDFs in intake/articles/ awaiting processing.

    Returns PDFs that haven't been extracted yet, plus those already extracted.
    Use this to see what's available before calling extract_pdf().
    """
    return preprocessing.list_intake_pdfs()


@mcp.tool()
def extract_pdf(filename: str) -> dict[str, Any]:
    """
    Submit PDF to Datalab Marker API and wait for completion.

    This is a blocking operation that typically takes 30-120 seconds.
    Requires DATALAB_API_KEY environment variable.

    Args:
        filename: Name of PDF in intake/articles/ (e.g., "smith-2024-pda.pdf")
    """
    return preprocessing.extract_pdf(filename)


@mcp.tool()
def parse_extracted_article(slug: str) -> dict[str, Any]:
    """
    Run mechanical parser on Datalab JSON, create structured article data.

    Extracts title, authors, abstract, body, references from the raw blocks.

    Args:
        slug: The article slug (filename without extension, slugified)
    """
    return preprocessing.parse_extracted_article(slug)


@mcp.tool()
def get_article_for_review(slug: str) -> dict[str, Any]:
    """
    Get parsed article data + raw blocks for Claude to review and classify.

    Returns:
    - parser_extracted: What the mechanical parser found (claims to verify)
    - raw_blocks: First 2 pages of content to verify extractions and derive classifications
    - classification_guide: Definitions for method/voice/peer_reviewed (no suggestions)

    Claude must:
    1. Verify parser extractions against raw_blocks (confirm or correct title/authors/abstract)
    2. Derive year from raw_blocks if not extracted
    3. Derive method by reading the content (empirical/synthesis/theoretical/lived_experience)
    4. Derive voice by reading the content (academic/practitioner/organization/individual)
    5. Derive peer_reviewed from evidence in raw_blocks (DOI, journal name, etc.)

    NO SUGGESTIONS PROVIDED. Claude must derive all classifications from the content.

    Args:
        slug: The article slug
    """
    return preprocessing.get_article_for_review(slug)


@mcp.tool()
def complete_article_review(
    slug: str,
    title: str,
    authors: str,
    year: str,
    abstract_confirmed: bool,
    method: str,
    voice: str,
    peer_reviewed: bool,
    corrected_abstract: str | None = None,
    citation: str | None = None,
    notes: str | None = None,
) -> dict[str, Any]:
    """
    Complete the article review by stating all values explicitly.

    You must STATE the title, authors, and year — not just confirm them.
    Compare parser_extracted with raw_blocks and write what you found.

    You must DERIVE method, voice, and peer_reviewed from reading the content.
    No suggestions are provided. Read the raw_blocks and decide.

    Args:
        slug: The article slug
        title: REQUIRED — state the title you found in raw_blocks
        authors: REQUIRED — state the authors (e.g., "E. O'Nions, J. Gould, P. Christie")
        year: REQUIRED — publication year (4 digits, e.g., "2018")
        abstract_confirmed: True if parser's abstract is correct, False if correcting
        method: REQUIRED — one of: empirical, synthesis, theoretical, lived_experience
        voice: REQUIRED — one of: academic, practitioner, organization, individual
        peer_reviewed: REQUIRED — True if peer-reviewed, False otherwise
        corrected_abstract: Only required if abstract_confirmed=False
        citation: Optional full citation string
        notes: Optional notes about issues or flags
    """
    return preprocessing.complete_article_review(
        slug=slug,
        title=title,
        authors=authors,
        year=year,
        abstract_confirmed=abstract_confirmed,
        method=method,
        voice=voice,
        peer_reviewed=peer_reviewed,
        corrected_abstract=corrected_abstract,
        citation=citation,
        notes=notes,
    )


@mcp.tool()
def get_body_for_review(slug: str, chunk: int = 0) -> dict[str, Any]:
    """
    Get body_html in chunks for structural review.

    Returns paragraphs with flagged issues:
    - ORPHAN: Starts lowercase — split from previous
    - INCOMPLETE: No ending punctuation — continues in next
    - CAPTION: Figure/table caption in body
    - PAGE_ARTIFACT: Page number/header leaked through
    - SHORT_FRAGMENT: Very short text — cruft?
    - REFERENCE_LEAK: Reference in body

    Call with chunk=0, then chunk=1, etc. until all reviewed.

    Args:
        slug: The article slug
        chunk: Which chunk to return (0-indexed, 10 paragraphs each)
    """
    return preprocessing.get_body_for_review(slug, chunk)


@mcp.tool()
def complete_body_review(
    slug: str,
    body_approved: bool,
    fixes: list[dict] | None = None,
    issues_acknowledged: list[int] | None = None,
    notes: str | None = None,
) -> dict[str, Any]:
    """
    Complete the body review — confirm clean or apply fixes.

    If get_body_for_review() flagged issues, you MUST either:
    1. Fix them (provide in fixes list), OR
    2. Acknowledge them as false positives (list indices in issues_acknowledged)

    You CANNOT approve the body while ignoring flagged issues.

    Args:
        slug: The article slug
        body_approved: True if body is clean (after fixes), False if issues remain
        fixes: List of fixes. Each is {"index": N, "action": ACTION}
            Actions: "join_previous", "join_next", "delete", "replace"
            For replace: {"index": N, "action": "replace", "text": "new text"}
        issues_acknowledged: List of paragraph indices where issues are false positives
        notes: Explain why acknowledged issues are false positives
    """
    return preprocessing.complete_body_review(
        slug=slug,
        body_approved=body_approved,
        fixes=fixes,
        issues_acknowledged=issues_acknowledged,
        notes=notes,
    )


@mcp.tool()
def get_preprocessing_status() -> dict[str, Any]:
    """
    Get overview of preprocessing pipeline status.

    Returns counts of PDFs in intake, extracted/parsed in cache,
    and database status by processing_status.
    """
    return preprocessing.get_preprocessing_status()


# --- Main entry point ---

def main():
    """Run the MCP server."""
    logger.info("Starting PDA Translation Machine MCP Server...")

    # Initialize on startup
    db = get_database()
    db.cleanup_expired_tokens()
    logger.info(f"Database at {db._path}")

    # Log taxonomy status
    taxonomy = get_taxonomy()
    logger.info(
        f"Taxonomy loaded: {len(taxonomy.methods)} methods, "
        f"{len(taxonomy.voices)} voices, {len(taxonomy.categories)} categories"
    )

    # Log progress
    progress = db.get_progress()
    logger.info(
        f"Progress: {progress['translated']} translated, "
        f"{progress['pending']} pending, {progress['skipped']} skipped"
    )

    # Run the server
    mcp.run()


if __name__ == "__main__":
    main()
