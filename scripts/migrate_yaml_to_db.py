#!/usr/bin/env python3
"""
Migrate data from pda_research.yaml to SQLite database.
"""

import sqlite3
import yaml
from pathlib import Path

DB_PATH = Path(__file__).parent.parent / "data" / "pda.db"
YAML_PATH = Path(__file__).parent.parent / "data" / "pda_research.yaml"
CATEGORIES_PATH = Path(__file__).parent.parent / "data" / "categories.yaml"


def migrate():
    """Migrate YAML data to SQLite."""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()

    # Load and insert categories
    with open(CATEGORIES_PATH) as f:
        categories_data = yaml.safe_load(f)

    for cat_id, cat in categories_data.get("categories", {}).items():
        cursor.execute(
            """
            INSERT OR REPLACE INTO categories (id, label_fr, label_en, description, url_slug, priority)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                cat_id,
                cat.get("label_fr", ""),
                cat.get("label_en", ""),
                cat.get("description", ""),
                cat.get("url_slug", cat_id),
                cat.get("priority", 0),
            ),
        )

    # Load and insert articles
    with open(YAML_PATH) as f:
        data = yaml.safe_load(f)

    for resource in data.get("resources", []):
        article_id = resource.get("id", "")

        # Insert article
        cursor.execute(
            """
            INSERT OR REPLACE INTO articles
            (id, source_language, source_title, source_url, authors, year, doi, open_access, summary_original)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                article_id,
                "en",  # All current sources are English
                resource.get("title_en", ""),
                resource.get("url", ""),
                resource.get("authors", ""),
                resource.get("year", ""),
                resource.get("doi", ""),
                1 if resource.get("open_access") else 0,
                resource.get("summary_en", ""),
            ),
        )

        # Insert keywords
        for keyword in resource.get("keywords", []):
            # Insert keyword if not exists
            cursor.execute(
                "INSERT OR IGNORE INTO keywords (keyword) VALUES (?)", (keyword,)
            )
            # Get keyword id
            cursor.execute("SELECT id FROM keywords WHERE keyword = ?", (keyword,))
            keyword_id = cursor.fetchone()[0]
            # Link to article
            cursor.execute(
                "INSERT OR IGNORE INTO article_keywords (article_id, keyword_id) VALUES (?, ?)",
                (article_id, keyword_id),
            )

        # Check if there's an existing French translation
        summary_fr = resource.get("summary_fr", "")
        title_fr = resource.get("title_fr", "")
        status = resource.get("translation_status", "pending")

        if summary_fr or title_fr:
            # Map old status to new
            status_map = {
                "completed": "translated",
                "not_started": "pending",
            }
            new_status = status_map.get(status, status)

            cursor.execute(
                """
                INSERT OR REPLACE INTO translations
                (article_id, target_language, translated_title, translated_summary, status)
                VALUES (?, ?, ?, ?, ?)
                """,
                (article_id, "fr", title_fr, summary_fr, new_status),
            )

    conn.commit()

    # Print stats
    cursor.execute("SELECT COUNT(*) FROM articles")
    article_count = cursor.fetchone()[0]
    cursor.execute("SELECT COUNT(*) FROM keywords")
    keyword_count = cursor.fetchone()[0]
    cursor.execute("SELECT COUNT(*) FROM translations WHERE status != 'pending'")
    translation_count = cursor.fetchone()[0]
    cursor.execute("SELECT COUNT(*) FROM categories")
    category_count = cursor.fetchone()[0]

    print(f"Migration complete:")
    print(f"  - {article_count} articles")
    print(f"  - {keyword_count} unique keywords")
    print(f"  - {translation_count} translations in progress")
    print(f"  - {category_count} categories")

    conn.close()


if __name__ == "__main__":
    migrate()
