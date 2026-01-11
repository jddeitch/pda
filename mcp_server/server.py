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
- ingest_article() — add new article from intake/ folder (Phase 2)

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
