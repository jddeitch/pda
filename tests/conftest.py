"""
Pytest fixtures for Translation Machine tests.

Provides:
- Fresh test database (isolated from production)
- Sample article data
- Cached PDF for extraction tests
- Glossary access
"""

import shutil
import sqlite3
from pathlib import Path

import pytest


# Paths
PROJECT_ROOT = Path(__file__).parent.parent
TEST_DATA_DIR = PROJECT_ROOT / "tests" / "data"
CACHE_DIR = PROJECT_ROOT / "cache" / "articles"


@pytest.fixture
def test_db(tmp_path):
    """
    Create a fresh test database with schema and sample data.

    Yields the database path. Cleans up after test.
    """
    db_path = tmp_path / "test_pda.db"

    # Create schema
    conn = sqlite3.connect(db_path)
    conn.executescript("""
        -- Articles table
        CREATE TABLE articles (
            id TEXT PRIMARY KEY,
            source_title TEXT NOT NULL,
            source_url TEXT,
            summary_original TEXT,
            open_access INTEGER DEFAULT 0,
            doi TEXT,
            processing_status TEXT DEFAULT 'pending',
            processed_at TEXT,
            method TEXT,
            voice TEXT,
            peer_reviewed INTEGER,
            source TEXT,
            processing_flags TEXT DEFAULT '[]',
            processing_notes TEXT,
            extraction_method TEXT,
            extraction_problems TEXT DEFAULT '[]',
            glossary_version TEXT,
            created_at TEXT DEFAULT (datetime('now'))
        );

        -- Translations table
        CREATE TABLE translations (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            article_id TEXT NOT NULL,
            target_language TEXT NOT NULL,
            translated_title TEXT,
            translated_summary TEXT,
            translated_full_text TEXT,
            status TEXT DEFAULT 'pending',
            updated_at TEXT,
            FOREIGN KEY (article_id) REFERENCES articles(id),
            UNIQUE(article_id, target_language)
        );

        -- Categories table
        CREATE TABLE categories (
            id TEXT PRIMARY KEY,
            name_en TEXT,
            name_fr TEXT,
            description TEXT
        );

        -- Article categories junction
        CREATE TABLE article_categories (
            article_id TEXT,
            category_id TEXT,
            is_primary INTEGER DEFAULT 0,
            PRIMARY KEY (article_id, category_id),
            FOREIGN KEY (article_id) REFERENCES articles(id),
            FOREIGN KEY (category_id) REFERENCES categories(id)
        );

        -- Keywords table
        CREATE TABLE keywords (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            keyword TEXT UNIQUE NOT NULL
        );

        -- Article keywords junction
        CREATE TABLE article_keywords (
            article_id TEXT,
            keyword_id INTEGER,
            PRIMARY KEY (article_id, keyword_id),
            FOREIGN KEY (article_id) REFERENCES articles(id),
            FOREIGN KEY (keyword_id) REFERENCES keywords(id)
        );

        -- Session state table
        CREATE TABLE session_state (
            id INTEGER PRIMARY KEY CHECK (id = 1),
            articles_processed_count INTEGER DEFAULT 0,
            human_review_interval INTEGER DEFAULT 5,
            last_reset_at TEXT DEFAULT (datetime('now', 'localtime')),
            last_reset_date TEXT DEFAULT (date('now', 'localtime'))
        );
        INSERT INTO session_state (id) VALUES (1);

        -- Validation tokens table
        CREATE TABLE validation_tokens (
            token TEXT PRIMARY KEY,
            article_id TEXT NOT NULL,
            classification_data TEXT NOT NULL,
            created_at TEXT NOT NULL DEFAULT (datetime('now')),
            used INTEGER DEFAULT 0,
            FOREIGN KEY (article_id) REFERENCES articles(id)
        );

        -- Categories seed data
        INSERT INTO categories (id, name_en, name_fr) VALUES
            ('fondements', 'Foundations', 'Fondements'),
            ('evaluation', 'Assessment', 'Évaluation'),
            ('presentation_clinique', 'Clinical Presentation', 'Présentation clinique'),
            ('etiologie', 'Etiology', 'Étiologie et mécanismes'),
            ('prise_en_charge', 'Management', 'Prise en charge'),
            ('comorbidites', 'Comorbidities', 'Comorbidités'),
            ('trajectoire', 'Developmental Trajectory', 'Trajectoire développementale');
    """)
    conn.commit()
    conn.close()

    yield db_path

    # Cleanup handled by tmp_path fixture


@pytest.fixture
def sample_articles(test_db):
    """Insert sample articles into test database."""
    conn = sqlite3.connect(test_db)

    articles = [
        ("test-article-1", "First Test Article", "https://example.com/1.pdf",
         "Summary of first article about PDA.", 1, "10.1234/test1", "pending"),
        ("test-article-2", "Second Test Article", "https://example.com/2.pdf",
         "Summary of second article.", 1, None, "pending"),
        ("test-article-3", "Third Test Article (Paywalled)", "https://example.com/3",
         "Summary only - paywalled.", 0, None, "pending"),
        ("test-article-4", "Already Translated", "https://example.com/4.pdf",
         "This one is done.", 1, None, "translated"),
        ("test-article-5", "Skipped Article", "https://example.com/5.pdf",
         "This was skipped.", 1, None, "skipped"),
    ]

    conn.executemany("""
        INSERT INTO articles (id, source_title, source_url, summary_original,
                              open_access, doi, processing_status)
        VALUES (?, ?, ?, ?, ?, ?, ?)
    """, articles)
    conn.commit()
    conn.close()

    return test_db


@pytest.fixture
def db_with_articles(sample_articles, monkeypatch):
    """
    Provide a database with sample articles and patch the module to use it.
    """
    from mcp_server import database

    # Create a new Database instance pointing to test db
    test_database = database.Database(sample_articles)
    test_database.run_migrations()

    # Patch the singleton
    monkeypatch.setattr(database, "_db", test_database)

    yield test_database

    # Cleanup
    test_database.close()


@pytest.fixture
def cached_pdf(tmp_path):
    """
    Create a cached PDF file for extraction tests.

    Uses the real O'Nions PDF if available, otherwise creates a minimal test file.
    """
    source_pdf = PROJECT_ROOT / "external" / "pda" / "An examination of the behavioural features associated with PDA (O'Nions 2013).pdf"

    if source_pdf.exists():
        # Copy real PDF to cache location
        cache_path = CACHE_DIR / "test-article-1.pdf"
        CACHE_DIR.mkdir(parents=True, exist_ok=True)
        shutil.copy(source_pdf, cache_path)
        yield cache_path
        # Cleanup
        if cache_path.exists():
            cache_path.unlink()
    else:
        # Create minimal test PDF (just for import testing)
        pytest.skip("Real PDF not available for extraction tests")


@pytest.fixture
def sample_text():
    """Sample English text for chunking and glossary tests."""
    return """This is the first paragraph about Pathological Demand Avoidance (PDA).
Children with PDA show demand avoidance and need for control.

This is the second paragraph discussing autism spectrum disorder and
differential diagnosis considerations.

The third paragraph covers avoidance strategies and mood lability
observed in clinical settings.

Fourth paragraph about assessment tools like the EDA-Q and
diagnostic interview approaches.

Fifth paragraph on management and therapeutic approaches
for children with PDA profile.

Sixth paragraph discussing comorbidities including anxiety
and emotional regulation difficulties.

Seventh paragraph about family experiences and
parenting strategies that may help.

Eighth paragraph covering educational approaches and
school exclusion prevention."""


@pytest.fixture
def clear_chunk_cache():
    """Clear the chunk cache before and after test."""
    from mcp_server.tools import clear_chunk_cache
    clear_chunk_cache()
    yield
    clear_chunk_cache()
