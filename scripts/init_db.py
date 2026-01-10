#!/usr/bin/env python3
"""
Initialize the PDA database schema.
"""

import sqlite3
from pathlib import Path

DB_PATH = Path(__file__).parent.parent / "data" / "pda.db"


def init_db():
    """Create the database schema."""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()

    # Categories table
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS categories (
            id TEXT PRIMARY KEY,
            label_fr TEXT NOT NULL,
            label_en TEXT NOT NULL,
            description TEXT,
            url_slug TEXT NOT NULL,
            priority INTEGER DEFAULT 0
        )
    """)

    # Articles table (source material)
    # method: empirical, synthesis, theoretical, lived_experience
    # voice: academic, practitioner, organization, individual
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS articles (
            id TEXT PRIMARY KEY,
            source_language TEXT DEFAULT 'en',
            source_title TEXT NOT NULL,
            source_url TEXT,
            authors TEXT,
            year TEXT,
            journal TEXT,
            doi TEXT,
            open_access INTEGER DEFAULT 0,
            peer_reviewed INTEGER DEFAULT 0,
            method TEXT,
            voice TEXT,
            summary_original TEXT,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT DEFAULT CURRENT_TIMESTAMP
        )
    """)

    # Article-category junction table (supports primary + secondary)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS article_categories (
            article_id TEXT NOT NULL,
            category_id TEXT NOT NULL,
            is_primary INTEGER DEFAULT 0,
            PRIMARY KEY (article_id, category_id),
            FOREIGN KEY (article_id) REFERENCES articles(id),
            FOREIGN KEY (category_id) REFERENCES categories(id)
        )
    """)

    # Keywords table
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS keywords (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            keyword TEXT UNIQUE NOT NULL
        )
    """)

    # Article-keyword junction table
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS article_keywords (
            article_id TEXT NOT NULL,
            keyword_id INTEGER NOT NULL,
            PRIMARY KEY (article_id, keyword_id),
            FOREIGN KEY (article_id) REFERENCES articles(id),
            FOREIGN KEY (keyword_id) REFERENCES keywords(id)
        )
    """)

    # Translations table (one per article per target language)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS translations (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            article_id TEXT NOT NULL,
            target_language TEXT NOT NULL,
            translated_title TEXT,
            translated_summary TEXT,
            translated_full_text TEXT,
            status TEXT DEFAULT 'pending',
            translator_notes TEXT,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(article_id, target_language),
            FOREIGN KEY (article_id) REFERENCES articles(id)
        )
    """)

    # Create indexes for common queries
    cursor.execute("""
        CREATE INDEX IF NOT EXISTS idx_translations_language
        ON translations(target_language)
    """)
    cursor.execute("""
        CREATE INDEX IF NOT EXISTS idx_translations_status
        ON translations(status)
    """)
    cursor.execute("""
        CREATE INDEX IF NOT EXISTS idx_article_categories_primary
        ON article_categories(is_primary)
    """)

    conn.commit()
    conn.close()
    print(f"Database initialized at {DB_PATH}")


if __name__ == "__main__":
    init_db()
