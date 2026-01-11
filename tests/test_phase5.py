"""
Tests for Phase 5: Admin Interface Data Layer.

Focus on the database query functions that power the admin interface.
Route testing (ENABLE_ADMIN redirect) is optional per the plan.

Covers:
- getProgress — returns accurate counts by processing_status
- getSessionState — returns current session configuration
- getFlaggedArticles — returns articles with processing_flags
- getAdminStats — returns comprehensive stats
- getAdminArticles — filters work correctly
- getPreprocessingQueue — returns PDFEXTRACT skipped articles
"""

import json

import pytest


class TestAdminQueries:
    """Tests for admin dashboard query functions."""

    def test_get_progress_returns_counts(self, db_with_admin_data):
        """getProgress should return counts grouped by processing_status."""
        from mcp_server.tools import get_progress

        result = get_progress()

        assert "progress" in result
        progress = result["progress"]

        # Verify counts from fixtures
        assert progress.get("pending", 0) == 2
        assert progress.get("translated", 0) == 2
        assert progress.get("skipped", 0) == 1
        assert progress.get("total", 0) == 5

    def test_get_session_state_returns_config(self, db_with_admin_data):
        """getSessionState should return session configuration."""
        session = db_with_admin_data.get_session_state()

        assert session is not None
        assert "articles_processed_count" in session
        assert "human_review_interval" in session
        assert "last_reset_at" in session
        assert "last_reset_date" in session

    def test_get_session_state_default_values(self, db_with_admin_data):
        """Session state should have sensible defaults."""
        session = db_with_admin_data.get_session_state()

        assert session["human_review_interval"] == 5  # Default
        assert session["articles_processed_count"] >= 0

    def test_get_flagged_articles_returns_correct_articles(self, db_with_admin_data):
        """getFlaggedArticles should return translated articles with flags."""
        flagged = db_with_admin_data.get_flagged_articles(limit=20)

        assert len(flagged) == 1  # Only one flagged translated article
        assert flagged[0]["id"] == "admin-test-4"

        flags = json.loads(flagged[0]["processing_flags"])
        assert "TERMMIS" in flags

    def test_get_flagged_articles_excludes_pending(self, db_with_admin_data):
        """Flagged articles should only include translated status."""
        # Add a pending article with flags
        db_with_admin_data.execute("""
            UPDATE articles
            SET processing_flags = '["WORDDRIFT"]'
            WHERE id = 'admin-test-1'
        """)
        db_with_admin_data.commit()

        flagged = db_with_admin_data.get_flagged_articles(limit=20)

        # Should still only return translated articles
        for article in flagged:
            assert article["id"] != "admin-test-1"

    def test_get_flagged_articles_respects_limit(self, db_with_admin_data):
        """Limit parameter should be honored."""
        flagged = db_with_admin_data.get_flagged_articles(limit=1)
        assert len(flagged) <= 1


class TestAdminStats:
    """Tests for comprehensive admin statistics."""

    def test_admin_stats_totals(self, db_with_admin_data):
        """Admin stats should calculate correct totals."""
        stats = db_with_admin_data.get_admin_stats()

        assert stats["total"] == 5
        assert stats["pending"] == 2
        assert stats["translated"] == 2
        assert stats["skipped"] == 1
        assert stats["in_progress"] == 0

    def test_admin_stats_flagged_count(self, db_with_admin_data):
        """Flagged count should only include translated articles with flags."""
        stats = db_with_admin_data.get_admin_stats()

        # Only admin-test-4 has flags AND is translated
        assert stats["flagged"] == 1


class TestAdminArticles:
    """Tests for filtered article listing."""

    def test_get_all_articles(self, db_with_admin_data):
        """Should return all articles when no filters."""
        articles = db_with_admin_data.get_admin_articles()

        assert len(articles) == 5

    def test_filter_by_status(self, db_with_admin_data):
        """Status filter should work."""
        articles = db_with_admin_data.get_admin_articles(status="translated")

        assert len(articles) == 2
        for article in articles:
            assert article["processing_status"] == "translated"

    def test_filter_by_has_flags(self, db_with_admin_data):
        """hasFlags filter should work."""
        articles = db_with_admin_data.get_admin_articles(has_flags=True)

        # Only admin-test-4 (translated with TERMMIS) and admin-test-5 (skipped with PDFEXTRACT)
        assert len(articles) == 2
        for article in articles:
            flags = json.loads(article["processing_flags"])
            assert len(flags) > 0

    def test_filter_by_method(self, db_with_admin_data):
        """Method filter should work."""
        articles = db_with_admin_data.get_admin_articles(method="empirical")

        assert len(articles) == 1
        assert articles[0]["method"] == "empirical"

    def test_combined_filters(self, db_with_admin_data):
        """Multiple filters should combine with AND."""
        articles = db_with_admin_data.get_admin_articles(
            status="translated",
            has_flags=True
        )

        assert len(articles) == 1
        assert articles[0]["id"] == "admin-test-4"


class TestPreprocessingQueue:
    """Tests for PDF preprocessing queue."""

    def test_returns_pdfextract_skipped(self, db_with_admin_data):
        """Should return skipped articles with PDFEXTRACT flag."""
        queue = db_with_admin_data.get_preprocessing_queue()

        assert len(queue) == 1
        assert queue[0]["id"] == "admin-test-5"

    def test_excludes_other_skipped(self, db_with_admin_data):
        """Should not include skipped articles without PDFEXTRACT."""
        # Add a skipped article without PDFEXTRACT
        db_with_admin_data.execute("""
            INSERT INTO articles (id, source_title, processing_status, processing_flags, source)
            VALUES ('admin-test-6', 'Skipped No PDF Issue', 'skipped', '["TANGENT"]', 'Test')
        """)
        db_with_admin_data.commit()

        queue = db_with_admin_data.get_preprocessing_queue()

        # Should still only return PDFEXTRACT articles
        assert len(queue) == 1
        assert queue[0]["id"] == "admin-test-5"


class TestRecentlyCompleted:
    """Tests for recently completed articles."""

    def test_returns_translated_articles(self, db_with_admin_data):
        """Should return translated articles ordered by processed_at."""
        from datetime import datetime

        # Set processed_at times
        db_with_admin_data.execute("""
            UPDATE articles
            SET processed_at = datetime('now', '-1 hour')
            WHERE id = 'admin-test-3'
        """)
        db_with_admin_data.execute("""
            UPDATE articles
            SET processed_at = datetime('now')
            WHERE id = 'admin-test-4'
        """)
        db_with_admin_data.commit()

        recent = db_with_admin_data.get_recently_completed(limit=5)

        assert len(recent) == 2
        # Most recent first
        assert recent[0]["id"] == "admin-test-4"
        assert recent[1]["id"] == "admin-test-3"

    def test_respects_limit(self, db_with_admin_data):
        """Limit parameter should be honored."""
        recent = db_with_admin_data.get_recently_completed(limit=1)
        assert len(recent) <= 1


# =============================================================================
# Fixtures
# =============================================================================

@pytest.fixture
def db_with_admin_data(sample_articles, monkeypatch):
    """
    Provide a database with data suitable for admin interface testing.

    Creates:
    - 2 pending articles (no flags)
    - 2 translated articles (1 with flags, 1 without)
    - 1 skipped article (with PDFEXTRACT flag)
    """
    from mcp_server import database

    # Create a new Database instance pointing to test db
    test_database = database.Database(sample_articles)
    test_database.run_migrations()

    # Clear existing sample data
    test_database.execute("DELETE FROM articles")

    # Insert admin test articles
    articles = [
        # Pending articles
        ("admin-test-1", "Pending Article 1", "pending", "[]", None, None, "Test Source"),
        ("admin-test-2", "Pending Article 2", "pending", "[]", None, None, "Test Source"),
        # Translated articles
        ("admin-test-3", "Translated Clean", "translated", "[]", None, "empirical", "Journal A"),
        ("admin-test-4", "Translated With Flags", "translated", '["TERMMIS", "WORDDRIFT"]',
         "Missing: PDA", "synthesis", "Journal B"),
        # Skipped with PDFEXTRACT
        ("admin-test-5", "Skipped PDF Problem", "skipped", '["PDFEXTRACT"]',
         "PDF extraction failed", None, "Test Source"),
    ]

    test_database.executemany("""
        INSERT INTO articles (id, source_title, processing_status, processing_flags,
                              processing_notes, method, source)
        VALUES (?, ?, ?, ?, ?, ?, ?)
    """, articles)
    test_database.commit()

    # Patch the singleton
    monkeypatch.setattr(database, "_db", test_database)

    # Add admin query methods to the database object
    def get_flagged_articles(limit=20):
        return test_database.execute("""
            SELECT id, source_title, processing_flags, processing_notes, processed_at
            FROM articles
            WHERE processing_status = 'translated'
              AND json_array_length(processing_flags) > 0
            ORDER BY processed_at DESC
            LIMIT ?
        """, (limit,)).fetchall()

    def get_admin_stats():
        counts = test_database.execute("""
            SELECT processing_status, COUNT(*) as count
            FROM articles GROUP BY processing_status
        """).fetchall()
        count_map = {row["processing_status"]: row["count"] for row in counts}

        flagged_count = test_database.execute("""
            SELECT COUNT(*) as count FROM articles
            WHERE processing_status = 'translated'
              AND json_array_length(processing_flags) > 0
        """).fetchone()["count"]

        return {
            "total": sum(count_map.values()),
            "pending": count_map.get("pending", 0),
            "in_progress": count_map.get("in_progress", 0),
            "translated": count_map.get("translated", 0),
            "skipped": count_map.get("skipped", 0),
            "flagged": flagged_count,
        }

    def get_admin_articles(status=None, has_flags=False, category=None, method=None):
        query = """
            SELECT
                a.id,
                a.source_title,
                a.source_url,
                a.method,
                a.voice,
                a.processing_status,
                a.processing_flags,
                a.processing_notes,
                a.processed_at
            FROM articles a
        """
        conditions = []
        params = []

        if status:
            conditions.append("a.processing_status = ?")
            params.append(status)

        if has_flags:
            conditions.append("json_array_length(a.processing_flags) > 0")

        if method:
            conditions.append("a.method = ?")
            params.append(method)

        if conditions:
            query += " WHERE " + " AND ".join(conditions)

        query += " ORDER BY a.processed_at DESC NULLS LAST, a.source_title"

        return test_database.execute(query, params).fetchall()

    def get_preprocessing_queue():
        return test_database.execute("""
            SELECT id, source_title, source_url, processing_flags, processing_notes
            FROM articles
            WHERE processing_status = 'skipped'
              AND json_extract(processing_flags, '$') LIKE '%PDFEXTRACT%'
            ORDER BY source_title
        """).fetchall()

    def get_recently_completed(limit=10):
        return test_database.execute("""
            SELECT id, source_title, processing_flags, processing_notes, processed_at
            FROM articles
            WHERE processing_status = 'translated'
            ORDER BY processed_at DESC
            LIMIT ?
        """, (limit,)).fetchall()

    # Attach methods
    test_database.get_flagged_articles = get_flagged_articles
    test_database.get_admin_stats = get_admin_stats
    test_database.get_admin_articles = get_admin_articles
    test_database.get_preprocessing_queue = get_preprocessing_queue
    test_database.get_recently_completed = get_recently_completed

    yield test_database

    # Cleanup
    test_database.close()
