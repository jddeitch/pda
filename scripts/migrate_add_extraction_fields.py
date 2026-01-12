#!/usr/bin/env python3
"""
Migration: Add extraction fields to articles table.

New columns:
- raw_html: Original Datalab output (backup)
- abstract: Extracted abstract from PDF
- body_html: Cleaned main content (cruft stripped, paragraphs joined)
- citation: Journal, volume, pages
- acknowledgements: Acknowledgements section
- references_json: JSON array of reference strings

Note: method and voice already exist and handle article classification
(empirical, synthesis, theoretical, lived_experience / academic, practitioner, etc.)
"""

import sqlite3
from pathlib import Path

DB_PATH = Path("/Users/jd/Projects/pda/data/pda.db")


def migrate():
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()

    # Check which columns already exist
    cursor.execute("PRAGMA table_info(articles)")
    existing_columns = {row[1] for row in cursor.fetchall()}

    new_columns = [
        ("raw_html", "TEXT"),
        ("abstract", "TEXT"),
        ("body_html", "TEXT"),
        ("citation", "TEXT"),
        ("acknowledgements", "TEXT"),
        ("references_json", "TEXT"),  # JSON array of reference strings
    ]

    added = []
    skipped = []

    for col_name, col_type in new_columns:
        if col_name in existing_columns:
            skipped.append(col_name)
        else:
            cursor.execute(f"ALTER TABLE articles ADD COLUMN {col_name} {col_type}")
            added.append(col_name)

    conn.commit()
    conn.close()

    print("Migration complete.")
    if added:
        print(f"  Added: {', '.join(added)}")
    if skipped:
        print(f"  Skipped (already exist): {', '.join(skipped)}")


if __name__ == "__main__":
    migrate()
