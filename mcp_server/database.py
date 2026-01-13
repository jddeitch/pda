"""
SQLite operations and session state management.

This module handles:
- Database connections
- Article queries (get next, get by id, update status)
- Session state (articles_processed_count, human_review_interval)
- Validation tokens (create, validate, use)
- Migrations for new tables

Per D6: Session state uses SQLite table with midnight auto-reset (local time).
Per D17: Validation tokens stored in SQLite with 30-minute expiry.
"""

from __future__ import annotations

import json
import secrets
import sqlite3
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Optional, List


# Paths
PROJECT_ROOT = Path(__file__).parent.parent
DB_PATH = PROJECT_ROOT / "data" / "pda.db"


class Database:
    """
    Database operations for the translation pipeline.

    Maintains a single connection per instance. In production,
    a new Database() is created per request or the connection
    is managed by the MCP server lifecycle.
    """

    def __init__(self, db_path: Path = DB_PATH):
        self._path = db_path
        self._conn: sqlite3.Connection | None = None

    def _get_conn(self) -> sqlite3.Connection:
        """Get or create database connection."""
        if self._conn is None:
            self._conn = sqlite3.connect(self._path)
            self._conn.row_factory = sqlite3.Row
            # Enable foreign keys
            self._conn.execute("PRAGMA foreign_keys = ON")
        return self._conn

    def close(self) -> None:
        """Close the database connection."""
        if self._conn:
            self._conn.close()
            self._conn = None

    def execute(self, sql: str, params: tuple = ()) -> sqlite3.Cursor:
        """Execute a SQL statement."""
        return self._get_conn().execute(sql, params)

    def executemany(self, sql: str, params_list: list[tuple]) -> sqlite3.Cursor:
        """Execute a SQL statement with multiple parameter sets."""
        return self._get_conn().executemany(sql, params_list)

    def commit(self) -> None:
        """Commit the current transaction."""
        self._get_conn().commit()

    def rollback(self) -> None:
        """Rollback the current transaction."""
        self._get_conn().rollback()

    # --- Migrations ---

    def run_migrations(self) -> None:
        """
        Run all necessary migrations.

        Called at server startup to ensure schema is up to date.
        Migrations are idempotent (safe to run multiple times).
        """
        self._migrate_session_state()
        self._migrate_validation_tokens()
        self._migrate_article_columns()
        self._migrate_batch_jobs()
        self.commit()

    def _migrate_session_state(self) -> None:
        """Create session_state table if not exists (per D6, D23)."""
        self.execute("""
            CREATE TABLE IF NOT EXISTS session_state (
                id INTEGER PRIMARY KEY CHECK (id = 1),
                articles_processed_count INTEGER DEFAULT 0,
                human_review_interval INTEGER DEFAULT 5,
                last_reset_at TEXT DEFAULT (datetime('now', 'localtime')),
                last_reset_date TEXT DEFAULT (date('now', 'localtime'))
            )
        """)
        # Insert singleton row if not exists
        self.execute("INSERT OR IGNORE INTO session_state (id) VALUES (1)")

    def _migrate_validation_tokens(self) -> None:
        """Create validation_tokens table if not exists (per D17)."""
        self.execute("""
            CREATE TABLE IF NOT EXISTS validation_tokens (
                token TEXT PRIMARY KEY,
                article_id TEXT NOT NULL,
                classification_data TEXT NOT NULL,
                created_at TEXT NOT NULL DEFAULT (datetime('now')),
                used INTEGER DEFAULT 0,
                FOREIGN KEY (article_id) REFERENCES articles(id)
            )
        """)

    def _migrate_article_columns(self) -> None:
        """
        Ensure articles table has all required columns.

        Per the plan, these columns should exist:
        - extraction_method TEXT
        - extraction_problems TEXT (JSON array)
        - glossary_version TEXT (per D27)
        """
        cursor = self.execute("PRAGMA table_info(articles)")
        existing = {row["name"] for row in cursor.fetchall()}

        migrations = [
            ("extraction_method", "ALTER TABLE articles ADD COLUMN extraction_method TEXT"),
            ("extraction_problems", "ALTER TABLE articles ADD COLUMN extraction_problems TEXT DEFAULT '[]'"),
            ("glossary_version", "ALTER TABLE articles ADD COLUMN glossary_version TEXT"),
            ("processing_flags", "ALTER TABLE articles ADD COLUMN processing_flags TEXT DEFAULT '[]'"),
            ("processing_notes", "ALTER TABLE articles ADD COLUMN processing_notes TEXT"),
            ("processed_at", "ALTER TABLE articles ADD COLUMN processed_at TEXT"),
            ("summary_original", "ALTER TABLE articles ADD COLUMN summary_original TEXT"),
        ]

        for col_name, sql in migrations:
            if col_name not in existing:
                self.execute(sql)

    def _migrate_batch_jobs(self) -> None:
        """Create batch_jobs and batch_job_events tables if not exist."""
        self.execute("""
            CREATE TABLE IF NOT EXISTS batch_jobs (
                id TEXT PRIMARY KEY,
                job_type TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'pending',
                target_count INTEGER,
                processed_count INTEGER DEFAULT 0,
                current_article TEXT,
                started_at TEXT,
                completed_at TEXT,
                pid INTEGER,
                error_message TEXT,
                log_path TEXT,
                created_at TEXT DEFAULT (datetime('now', 'localtime'))
            )
        """)
        self.execute("""
            CREATE TABLE IF NOT EXISTS batch_job_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                job_id TEXT NOT NULL,
                event_type TEXT NOT NULL,
                article_slug TEXT,
                message TEXT,
                timestamp TEXT DEFAULT (datetime('now', 'localtime')),
                FOREIGN KEY (job_id) REFERENCES batch_jobs(id)
            )
        """)
        # Index for quick status lookups
        self.execute("""
            CREATE INDEX IF NOT EXISTS idx_batch_jobs_status ON batch_jobs(status)
        """)

    # --- Session State (per D6, D23) ---

    def get_session_state(self) -> dict[str, Any]:
        """Get current session state."""
        row = self.execute(
            "SELECT * FROM session_state WHERE id = 1"
        ).fetchone()
        if row:
            return dict(row)
        return {
            "articles_processed_count": 0,
            "human_review_interval": 5,
            "last_reset_at": None,
            "last_reset_date": None,
        }

    def check_session_limit(self) -> bool:
        """
        Check if SESSION_PAUSE should be returned.

        Returns True if articles_processed_count >= human_review_interval.
        Auto-resets at local midnight.
        """
        state = self.get_session_state()

        # Auto-reset at midnight (local time per D23)
        today_local = date.today().isoformat()
        if state["last_reset_date"] != today_local:
            self.execute("""
                UPDATE session_state
                SET articles_processed_count = 0,
                    last_reset_date = date('now', 'localtime'),
                    last_reset_at = datetime('now', 'localtime')
                WHERE id = 1
            """)
            self.commit()
            return False

        return state["articles_processed_count"] >= state["human_review_interval"]

    def increment_session_count(self, auto_commit: bool = True) -> None:
        """
        Increment articles_processed_count after successful save.

        Args:
            auto_commit: If True (default), commits immediately.
                         If False, caller is responsible for commit.
        """
        self.execute("""
            UPDATE session_state
            SET articles_processed_count = articles_processed_count + 1
            WHERE id = 1
        """)
        if auto_commit:
            self.commit()

    def reset_session_counter(self) -> dict[str, Any]:
        """Reset the session counter. Called after human review."""
        self.execute("""
            UPDATE session_state
            SET articles_processed_count = 0,
                last_reset_at = datetime('now', 'localtime')
            WHERE id = 1
        """)
        self.commit()
        return {"success": True, "message": "Session counter reset."}

    def set_human_review_interval(self, interval: int) -> dict[str, Any]:
        """Set the human review interval (1-20)."""
        if not 1 <= interval <= 20:
            return {"success": False, "error": "Interval must be between 1 and 20."}

        self.execute(
            "UPDATE session_state SET human_review_interval = ? WHERE id = 1",
            (interval,)
        )
        self.commit()
        return {"success": True, "interval": interval}

    # --- Validation Tokens (per D8, D17) ---

    def create_validation_token(
        self,
        article_id: str,
        classification_data: dict[str, Any]
    ) -> str:
        """
        Create a validation token for an article.

        Token is single-use and expires after 30 minutes.
        Classification data is stored with the token.
        """
        token = secrets.token_hex(16)
        self.execute(
            """
            INSERT INTO validation_tokens (token, article_id, classification_data, created_at)
            VALUES (?, ?, ?, datetime('now'))
            """,
            (token, article_id, json.dumps(classification_data))
        )
        self.commit()
        return token

    def validate_token(self, token: str, article_id: str) -> dict[str, Any]:
        """
        Validate a token for an article.

        Returns classification_data if valid, error dict if invalid.
        Token must:
        - Exist
        - Match article_id
        - Not be used
        - Not be expired (30 minutes)
        """
        row = self.execute(
            "SELECT * FROM validation_tokens WHERE token = ?",
            (token,)
        ).fetchone()

        if not row:
            return {"valid": False, "error": "INVALID_TOKEN", "message": "Token not found."}

        if row["article_id"] != article_id:
            return {"valid": False, "error": "INVALID_TOKEN", "message": "Token does not match article."}

        if row["used"]:
            return {"valid": False, "error": "INVALID_TOKEN", "message": "Token already used."}

        # Check expiry (30 minutes per D8)
        # Token created_at uses UTC (SQLite datetime('now')), so compare with utcnow()
        created = datetime.fromisoformat(row["created_at"])
        if datetime.utcnow() - created > timedelta(minutes=30):
            return {"valid": False, "error": "INVALID_TOKEN", "message": "Token expired."}

        return {
            "valid": True,
            "classification_data": json.loads(row["classification_data"])
        }

    def mark_token_used(self, token: str, auto_commit: bool = True) -> None:
        """
        Mark a token as used after successful save.

        Args:
            token: The validation token to mark as used.
            auto_commit: If True (default), commits immediately.
                         If False, caller is responsible for commit.
        """
        self.execute(
            "UPDATE validation_tokens SET used = 1 WHERE token = ?",
            (token,)
        )
        if auto_commit:
            self.commit()

    def cleanup_expired_tokens(self) -> int:
        """
        Remove expired and used tokens.

        Called periodically or on server start.
        Returns count of deleted tokens.
        """
        cursor = self.execute("""
            DELETE FROM validation_tokens
            WHERE used = 1
               OR created_at < datetime('now', '-1 hour')
        """)
        self.commit()
        return cursor.rowcount

    # --- Article Queries ---

    def get_progress(self) -> dict[str, int]:
        """Get counts by processing_status."""
        cursor = self.execute("""
            SELECT processing_status, COUNT(*) as count
            FROM articles
            GROUP BY processing_status
        """)
        counts = {row["processing_status"]: row["count"] for row in cursor.fetchall()}

        # Ensure all statuses are present
        for status in ("preprocessing", "pending", "pending_url", "in_progress", "translated", "skipped"):
            counts.setdefault(status, 0)

        return counts

    def get_next_article(self) -> dict[str, Any] | None:
        """
        Get the next article to process.

        Priority:
        1. in_progress (crash recovery) — restart from beginning per D1
        2. pending

        Returns article dict or None if no articles available.
        """
        # First check for in_progress (crash recovery)
        row = self.execute("""
            SELECT id, source_title, source_url, summary_original, open_access, doi
            FROM articles
            WHERE processing_status = 'in_progress'
            ORDER BY created_at ASC
            LIMIT 1
        """).fetchone()

        if not row:
            # Get next pending
            row = self.execute("""
                SELECT id, source_title, source_url, summary_original, open_access, doi
                FROM articles
                WHERE processing_status = 'pending'
                ORDER BY created_at ASC
                LIMIT 1
            """).fetchone()

        if not row:
            return None

        # Mark as in_progress
        self.execute(
            "UPDATE articles SET processing_status = 'in_progress' WHERE id = ?",
            (row["id"],)
        )
        self.commit()

        return {
            "id": row["id"],
            "source_title": row["source_title"],
            "source_url": row["source_url"],
            "summary_original": row["summary_original"],
            "open_access": bool(row["open_access"]),
            "doi": row["doi"],
        }

    def get_article_by_id(self, article_id: str) -> dict[str, Any] | None:
        """Get an article by ID."""
        row = self.execute(
            "SELECT * FROM articles WHERE id = ?",
            (article_id,)
        ).fetchone()
        return dict(row) if row else None

    def mark_article_translated(
        self,
        article_id: str,
        method: str,
        voice: str,
        peer_reviewed: bool,
        source: str,
        processing_flags: list[str],
        processing_notes: str,
        extraction_method: str | None = None,
        extraction_problems: list[str] | None = None,
        glossary_version: str | None = None,
    ) -> None:
        """
        Mark article as translated and update classification fields.

        Called within a transaction by save_article().
        """
        self.execute(
            """
            UPDATE articles
            SET processing_status = 'translated',
                processed_at = datetime('now'),
                method = ?,
                voice = ?,
                peer_reviewed = ?,
                source = ?,
                processing_flags = ?,
                processing_notes = ?,
                extraction_method = ?,
                extraction_problems = ?,
                glossary_version = ?
            WHERE id = ?
            """,
            (
                method,
                voice,
                1 if peer_reviewed else 0,
                source,
                json.dumps(processing_flags),
                processing_notes,
                extraction_method,
                json.dumps(extraction_problems or []),
                glossary_version,
                article_id,
            )
        )

    def mark_article_skipped(
        self,
        article_id: str,
        reason: str,
        flag_code: str
    ) -> dict[str, Any]:
        """
        Mark article as skipped (per D9).

        Does NOT increment session counter.
        """
        self.execute(
            """
            UPDATE articles
            SET processing_status = 'skipped',
                processing_notes = ?,
                processing_flags = ?
            WHERE id = ?
            """,
            (reason, json.dumps([flag_code]), article_id)
        )
        self.commit()
        return {"success": True, "article_id": article_id}

    def create_article(
        self,
        article_id: str,
        source_title: str,
        source_url: str | None,
        summary_original: str | None,
        doi: str | None,
        source: str | None,
        open_access: bool,
        processing_status: str = "pending",
    ) -> None:
        """
        Create a new article record.

        Used by ingest_article() for PDFs from intake/ folder.
        Default status is 'pending' — ready for translation immediately.
        """
        self.execute(
            """
            INSERT INTO articles (
                id, source_title, source_url, summary_original,
                doi, source, open_access, processing_status, created_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, datetime('now'))
            """,
            (
                article_id,
                source_title,
                source_url,
                summary_original,
                doi,
                source,
                1 if open_access else 0,
                processing_status,
            )
        )
        self.commit()

    def article_exists(self, article_id: str) -> bool:
        """Check if an article with this ID already exists."""
        row = self.execute(
            "SELECT 1 FROM articles WHERE id = ?",
            (article_id,)
        ).fetchone()
        return row is not None

    def confirm_article_url(self, article_id: str, source_url: str) -> dict[str, Any]:
        """
        Confirm the source URL for an article and move to pending status.

        Only works for articles with status 'pending_url'.

        Returns:
            Success dict or error dict with details.
        """
        # Get current article
        article = self.get_article_by_id(article_id)
        if not article:
            return {
                "success": False,
                "error": "NOT_FOUND",
                "details": f"Article '{article_id}' not found.",
            }

        if article["processing_status"] != "pending_url":
            return {
                "success": False,
                "error": "INVALID_STATUS",
                "details": f"Article status is '{article['processing_status']}', expected 'pending_url'.",
            }

        # Basic URL validation
        if not source_url or not source_url.startswith(("http://", "https://")):
            return {
                "success": False,
                "error": "INVALID_URL",
                "details": "URL must start with http:// or https://",
            }

        # Update article
        self.execute(
            """
            UPDATE articles
            SET source_url = ?,
                processing_status = 'pending'
            WHERE id = ?
            """,
            (source_url, article_id)
        )
        self.commit()

        return {
            "success": True,
            "article_id": article_id,
            "source_url": source_url,
            "message": "Article added to translation queue.",
        }

    def get_pending_url_articles(self) -> list[dict[str, Any]]:
        """Get all articles awaiting URL confirmation."""
        cursor = self.execute("""
            SELECT id, source_title, doi, source_url
            FROM articles
            WHERE processing_status = 'pending_url'
            ORDER BY created_at ASC
        """)
        return [dict(row) for row in cursor.fetchall()]

    # --- Preprocessing Operations ---

    def create_preprocessing_article(
        self,
        article_id: str,
        source_title: str,
        authors: str,
        abstract: str,
        body_html: str,
        doi: str | None,
        citation: str | None,
        year: str | None,
        method: str,
        voice: str,
        peer_reviewed: bool,
        references_json: str | None,
        source_url: str | None = None,
    ) -> dict[str, Any]:
        """
        Create article record with status='preprocessing' for human review.

        This is used by the preprocessing pipeline to create articles that
        need human approval before entering the translation queue.
        """
        # Check for duplicates
        if self.article_exists(article_id):
            return {
                "success": False,
                "error": "DUPLICATE",
                "details": f"Article '{article_id}' already exists.",
            }

        self.execute(
            """
            INSERT INTO articles (
                id, source_title, source_url, authors, year,
                doi, citation, abstract, body_html, references_json,
                method, voice, peer_reviewed, source,
                open_access, processing_status, created_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'preprocessing-mcp', 1, 'preprocessing', datetime('now'))
            """,
            (
                article_id,
                source_title,
                source_url,
                authors,
                year,
                doi,
                citation,
                abstract,
                body_html,
                references_json,
                method,
                voice,
                1 if peer_reviewed else 0,
            )
        )
        self.commit()
        return {"success": True, "article_id": article_id}

    # --- Translation Operations ---

    def save_translation(
        self,
        article_id: str,
        target_language: str,
        translated_title: str,
        translated_summary: str,
        translated_full_text: str | None,
    ) -> None:
        """
        Save or update translation for an article.

        Uses INSERT OR REPLACE for upsert behavior.
        """
        self.execute(
            """
            INSERT INTO translations (
                article_id, target_language, translated_title,
                translated_summary, translated_full_text, status, updated_at
            )
            VALUES (?, ?, ?, ?, ?, 'translated', datetime('now'))
            ON CONFLICT(article_id, target_language) DO UPDATE SET
                translated_title = excluded.translated_title,
                translated_summary = excluded.translated_summary,
                translated_full_text = excluded.translated_full_text,
                status = 'translated',
                updated_at = datetime('now')
            """,
            (article_id, target_language, translated_title, translated_summary, translated_full_text)
        )

    # --- Category Operations ---

    def set_article_categories(
        self,
        article_id: str,
        primary_category: str,
        secondary_categories: list[str]
    ) -> None:
        """
        Set categories for an article.

        Clears existing and inserts new.
        """
        # Clear existing
        self.execute(
            "DELETE FROM article_categories WHERE article_id = ?",
            (article_id,)
        )

        # Insert primary
        self.execute(
            "INSERT INTO article_categories (article_id, category_id, is_primary) VALUES (?, ?, 1)",
            (article_id, primary_category)
        )

        # Insert secondary
        for cat in secondary_categories:
            self.execute(
                "INSERT INTO article_categories (article_id, category_id, is_primary) VALUES (?, ?, 0)",
                (article_id, cat)
            )

    # --- Keyword Operations ---

    def set_article_keywords(self, article_id: str, keywords: list[str]) -> None:
        """
        Set keywords for an article.

        Creates keywords if they don't exist, then links them.
        """
        # Clear existing links
        self.execute(
            "DELETE FROM article_keywords WHERE article_id = ?",
            (article_id,)
        )

        for keyword in keywords:
            # Insert keyword if not exists
            self.execute(
                "INSERT OR IGNORE INTO keywords (keyword) VALUES (?)",
                (keyword,)
            )

            # Get keyword id
            row = self.execute(
                "SELECT id FROM keywords WHERE keyword = ?",
                (keyword,)
            ).fetchone()

            if row:
                # Link to article
                self.execute(
                    "INSERT INTO article_keywords (article_id, keyword_id) VALUES (?, ?)",
                    (article_id, row["id"])
                )


    # --- Batch Job Operations ---

    def create_batch_job(
        self,
        job_id: str,
        job_type: str,
        target_count: int,
        log_path: str,
    ) -> dict[str, Any]:
        """Create a new batch job record."""
        self.execute(
            """
            INSERT INTO batch_jobs (id, job_type, status, target_count, log_path, created_at)
            VALUES (?, ?, 'pending', ?, ?, datetime('now', 'localtime'))
            """,
            (job_id, job_type, target_count, log_path)
        )
        self.commit()
        return {"success": True, "job_id": job_id}

    def update_batch_job_status(
        self,
        job_id: str,
        status: str,
        pid: int | None = None,
        error_message: str | None = None,
        current_article: str | None = None,
    ) -> None:
        """Update batch job status and optional fields."""
        updates = ["status = ?"]
        params: list[Any] = [status]

        if status == "running":
            updates.append("started_at = datetime('now', 'localtime')")
        elif status in ("completed", "failed", "cancelled"):
            updates.append("completed_at = datetime('now', 'localtime')")

        if pid is not None:
            updates.append("pid = ?")
            params.append(pid)

        if error_message is not None:
            updates.append("error_message = ?")
            params.append(error_message)

        if current_article is not None:
            updates.append("current_article = ?")
            params.append(current_article)

        params.append(job_id)
        sql = f"UPDATE batch_jobs SET {', '.join(updates)} WHERE id = ?"
        self.execute(sql, tuple(params))
        self.commit()

    def increment_batch_job_progress(self, job_id: str) -> None:
        """Increment processed_count for a batch job."""
        self.execute(
            "UPDATE batch_jobs SET processed_count = processed_count + 1 WHERE id = ?",
            (job_id,)
        )
        self.commit()

    def add_batch_job_event(
        self,
        job_id: str,
        event_type: str,
        article_slug: str | None = None,
        message: str | None = None,
    ) -> None:
        """Add an event to the batch job log."""
        self.execute(
            """
            INSERT INTO batch_job_events (job_id, event_type, article_slug, message)
            VALUES (?, ?, ?, ?)
            """,
            (job_id, event_type, article_slug, message)
        )
        self.commit()

    def get_batch_job(self, job_id: str) -> dict[str, Any] | None:
        """Get a batch job by ID."""
        row = self.execute(
            "SELECT * FROM batch_jobs WHERE id = ?",
            (job_id,)
        ).fetchone()
        return dict(row) if row else None

    def get_batch_job_events(
        self,
        job_id: str,
        limit: int = 50
    ) -> list[dict[str, Any]]:
        """Get recent events for a batch job."""
        cursor = self.execute(
            """
            SELECT * FROM batch_job_events
            WHERE job_id = ?
            ORDER BY timestamp DESC
            LIMIT ?
            """,
            (job_id, limit)
        )
        return [dict(row) for row in cursor.fetchall()]

    def get_running_batch_job(self) -> dict[str, Any] | None:
        """Get the currently running batch job, if any."""
        row = self.execute(
            "SELECT * FROM batch_jobs WHERE status = 'running' LIMIT 1"
        ).fetchone()
        return dict(row) if row else None

    def get_recent_batch_jobs(self, limit: int = 10) -> list[dict[str, Any]]:
        """Get recent batch jobs for the admin dashboard."""
        cursor = self.execute(
            """
            SELECT * FROM batch_jobs
            ORDER BY created_at DESC
            LIMIT ?
            """,
            (limit,)
        )
        return [dict(row) for row in cursor.fetchall()]


# Module-level convenience function
_db: Database | None = None


def get_database() -> Database:
    """Get the database singleton."""
    global _db
    if _db is None:
        _db = Database()
        _db.run_migrations()
    return _db
