"""
MCP Tool implementations for the Translation Machine.

Phase 1 tools:
- get_next_article() — returns next article + taxonomy + workflow reminder
- get_progress() — returns status counts

Later phases will add:
- get_chunk()
- validate_classification()
- save_article()
- skip_article()
- set_human_review_interval()
- reset_session_counter()
- ingest_article()
"""

from typing import Any

from .database import get_database
from .taxonomy import get_taxonomy


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

    return db.mark_article_skipped(article_id, reason, flag_code)
