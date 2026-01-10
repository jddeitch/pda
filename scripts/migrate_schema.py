#!/usr/bin/env python3
"""
One-time migration to add method, voice, and peer_reviewed columns to articles table.
"""

import sqlite3
from pathlib import Path

DB_PATH = Path(__file__).parent.parent / "data" / "pda.db"


def migrate():
    """Add method, voice, and peer_reviewed columns to articles table."""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()

    # Check which columns already exist
    cursor.execute("PRAGMA table_info(articles)")
    existing_columns = {row[1] for row in cursor.fetchall()}

    migrations = []

    if "method" not in existing_columns:
        migrations.append(("method", "ALTER TABLE articles ADD COLUMN method TEXT"))

    if "voice" not in existing_columns:
        migrations.append(("voice", "ALTER TABLE articles ADD COLUMN voice TEXT"))

    if "peer_reviewed" not in existing_columns:
        migrations.append(
            (
                "peer_reviewed",
                "ALTER TABLE articles ADD COLUMN peer_reviewed INTEGER DEFAULT 0",
            )
        )

    if not migrations:
        print("All columns already exist. Nothing to migrate.")
        conn.close()
        return

    for column_name, sql in migrations:
        print(f"Adding column: {column_name}")
        cursor.execute(sql)

    conn.commit()
    conn.close()
    print(f"Migration complete. Added {len(migrations)} column(s).")


if __name__ == "__main__":
    migrate()
