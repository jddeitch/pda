"""
Tests for Phase 1 tools.

Covers:
- get_next_article() — returns next article, handles SESSION_PAUSE, COMPLETE
- get_progress() — returns accurate counts
- skip_article() — marks article skipped, validates flag codes
- set_human_review_interval() — validates range, persists setting
- reset_session_counter() — resets count
- Session state — midnight auto-reset, counter increment
"""

import json
import pytest


class TestGetNextArticle:
    """Tests for get_next_article() tool."""

    def test_returns_pending_article(self, db_with_articles):
        """Should return a pending article with taxonomy and workflow reminder."""
        from mcp_server.tools import get_next_article

        result = get_next_article()

        assert "article" in result
        assert result["article"]["id"] == "test-article-1"
        assert result["article"]["source_title"] == "First Test Article"
        assert result["article"]["open_access"] is True
        assert "taxonomy" in result
        assert "workflow_reminder" in result

    def test_taxonomy_contains_required_fields(self, db_with_articles):
        """Taxonomy should include methods, voices, and categories."""
        from mcp_server.tools import get_next_article

        result = get_next_article()
        taxonomy = result["taxonomy"]

        assert "methods" in taxonomy
        assert "voices" in taxonomy
        assert "categories" in taxonomy
        assert len(taxonomy["methods"]) == 4  # empirical, synthesis, theoretical, lived_experience
        assert len(taxonomy["voices"]) == 4   # academic, practitioner, organization, individual

    def test_marks_article_in_progress(self, db_with_articles):
        """Getting an article should mark it as in_progress."""
        from mcp_server.tools import get_next_article

        result = get_next_article()
        article_id = result["article"]["id"]

        # Check database
        article = db_with_articles.get_article_by_id(article_id)
        assert article["processing_status"] == "in_progress"

    def test_prioritizes_in_progress_over_pending(self, db_with_articles):
        """Should return in_progress article first (crash recovery)."""
        from mcp_server.tools import get_next_article

        # First call marks test-article-1 as in_progress
        result1 = get_next_article()
        first_id = result1["article"]["id"]

        # Second call should return same article (in_progress takes priority)
        result2 = get_next_article()
        assert result2["article"]["id"] == first_id

    def test_session_pause_when_limit_reached(self, db_with_articles):
        """Should return SESSION_PAUSE when review interval reached."""
        from mcp_server.tools import get_next_article, set_human_review_interval

        # Set interval to 1
        set_human_review_interval(1)

        # Simulate having processed 1 article
        db_with_articles.increment_session_count()

        result = get_next_article()

        assert result.get("status") == "SESSION_PAUSE"
        assert "articles_processed" in result
        assert "message" in result

    def test_complete_when_all_done(self, db_with_articles):
        """Should return COMPLETE when no pending/in_progress articles."""
        from mcp_server.tools import get_next_article

        # Mark all pending articles as translated
        db_with_articles.execute(
            "UPDATE articles SET processing_status = 'translated' WHERE processing_status = 'pending'"
        )
        db_with_articles.commit()

        result = get_next_article()

        assert result.get("status") == "COMPLETE"
        assert "translated" in result
        assert "skipped" in result


class TestGetProgress:
    """Tests for get_progress() tool."""

    def test_returns_accurate_counts(self, db_with_articles):
        """Should return correct counts for each status."""
        from mcp_server.tools import get_progress

        result = get_progress()

        assert result["progress"]["pending"] == 3
        assert result["progress"]["translated"] == 1
        assert result["progress"]["skipped"] == 1
        assert result["progress"]["total"] == 5

    def test_includes_session_state(self, db_with_articles):
        """Should include session state information."""
        from mcp_server.tools import get_progress

        result = get_progress()

        assert "session" in result
        assert "articles_processed_count" in result["session"]
        assert "human_review_interval" in result["session"]
        assert "remaining_before_pause" in result["session"]


class TestSkipArticle:
    """Tests for skip_article() tool."""

    def test_marks_article_skipped(self, db_with_articles):
        """Should mark article as skipped with reason and flag."""
        from mcp_server.tools import skip_article

        result = skip_article("test-article-1", "PDF extraction failed", "PDFEXTRACT")

        assert result["success"] is True
        assert result["article_id"] == "test-article-1"

        # Verify in database
        article = db_with_articles.get_article_by_id("test-article-1")
        assert article["processing_status"] == "skipped"
        assert article["processing_notes"] == "PDF extraction failed"
        assert "PDFEXTRACT" in article["processing_flags"]

    def test_rejects_invalid_flag_code(self, db_with_articles):
        """Should reject invalid flag codes."""
        from mcp_server.tools import skip_article

        result = skip_article("test-article-1", "Some reason", "INVALID_FLAG")

        assert result["success"] is False
        assert "error" in result
        assert "valid_flags" in result

    def test_does_not_increment_session_counter(self, db_with_articles):
        """Skipping should NOT increment session counter."""
        from mcp_server.tools import skip_article, get_progress

        before = get_progress()["session"]["articles_processed_count"]

        skip_article("test-article-1", "Skipping", "SKIP")

        after = get_progress()["session"]["articles_processed_count"]
        assert after == before


class TestSessionManagement:
    """Tests for session state management."""

    def test_set_human_review_interval_valid(self, db_with_articles):
        """Should accept valid interval values."""
        from mcp_server.tools import set_human_review_interval, get_progress

        result = set_human_review_interval(10)

        assert result["success"] is True
        assert result["interval"] == 10

        # Verify persisted
        progress = get_progress()
        assert progress["session"]["human_review_interval"] == 10

    def test_set_human_review_interval_invalid(self, db_with_articles):
        """Should reject out-of-range values."""
        from mcp_server.tools import set_human_review_interval

        result_low = set_human_review_interval(0)
        assert result_low["success"] is False

        result_high = set_human_review_interval(21)
        assert result_high["success"] is False

    def test_reset_session_counter(self, db_with_articles):
        """Should reset counter to zero."""
        from mcp_server.tools import reset_session_counter, get_progress

        # Increment counter first
        db_with_articles.increment_session_count()
        db_with_articles.increment_session_count()

        before = get_progress()["session"]["articles_processed_count"]
        assert before == 2

        result = reset_session_counter()
        assert result["success"] is True

        after = get_progress()["session"]["articles_processed_count"]
        assert after == 0


class TestTaxonomy:
    """Tests for taxonomy loading and validation."""

    def test_taxonomy_loads(self):
        """Taxonomy should load from YAML without errors."""
        from mcp_server.taxonomy import get_taxonomy

        taxonomy = get_taxonomy()

        assert len(taxonomy.methods) > 0
        assert len(taxonomy.voices) > 0
        assert len(taxonomy.categories) > 0

    def test_valid_method_check(self):
        """Should correctly validate method values."""
        from mcp_server.taxonomy import get_taxonomy

        taxonomy = get_taxonomy()

        assert taxonomy.is_valid_method("empirical") is True
        assert taxonomy.is_valid_method("synthesis") is True
        assert taxonomy.is_valid_method("invalid_method") is False

    def test_valid_voice_check(self):
        """Should correctly validate voice values."""
        from mcp_server.taxonomy import get_taxonomy

        taxonomy = get_taxonomy()

        assert taxonomy.is_valid_voice("academic") is True
        assert taxonomy.is_valid_voice("practitioner") is True
        assert taxonomy.is_valid_voice("invalid_voice") is False

    def test_valid_flag_check(self):
        """Should correctly validate flag codes."""
        from mcp_server.taxonomy import get_taxonomy

        taxonomy = get_taxonomy()

        assert taxonomy.is_valid_flag("PDFEXTRACT") is True
        assert taxonomy.is_valid_flag("SENTMIS") is True
        assert taxonomy.is_valid_flag("INVALID_FLAG") is False

    def test_blocking_flags(self):
        """Should identify blocking flags correctly."""
        from mcp_server.taxonomy import get_taxonomy

        taxonomy = get_taxonomy()
        blocking = taxonomy.get_blocking_flags()

        assert "SENTMIS" in blocking
        assert "WORDMIS" in blocking


class TestDatabase:
    """Tests for database operations."""

    def test_validation_token_lifecycle(self, db_with_articles):
        """Token should be created, validated, and marked used."""
        classification = {"method": "empirical", "voice": "academic"}

        # Create token
        token = db_with_articles.create_validation_token("test-article-1", classification)
        assert len(token) == 32  # hex(16 bytes)

        # Validate token
        result = db_with_articles.validate_token(token, "test-article-1")
        assert result["valid"] is True
        assert result["classification_data"] == classification

        # Mark used
        db_with_articles.mark_token_used(token)

        # Should fail validation now
        result2 = db_with_articles.validate_token(token, "test-article-1")
        assert result2["valid"] is False
        assert result2["error"] == "INVALID_TOKEN"

    def test_token_wrong_article(self, db_with_articles):
        """Token should fail if article_id doesn't match."""
        token = db_with_articles.create_validation_token("test-article-1", {})

        result = db_with_articles.validate_token(token, "test-article-2")
        assert result["valid"] is False

    def test_cleanup_expired_tokens(self, db_with_articles):
        """Should clean up expired and used tokens."""
        # Create and use a token
        token = db_with_articles.create_validation_token("test-article-1", {})
        db_with_articles.mark_token_used(token)

        # Cleanup
        deleted = db_with_articles.cleanup_expired_tokens()
        assert deleted >= 1
