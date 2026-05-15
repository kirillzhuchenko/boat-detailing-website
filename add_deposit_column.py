"""
add_deposit_columns.py
──────────────────────
Run this ONCE from your project root to add deposit tracking
columns to your existing database. Safe to run — it checks
whether the column already exists before doing anything.

Usage:
    python add_deposit_columns.py
"""

import sqlite3
import os

DB_PATH = os.environ.get("DATABASE_URL", "instance/bluewater.db")


def column_exists(conn, table, column):
    rows = conn.execute(f"PRAGMA table_info({table})").fetchall()
    return any(row[1] == column for row in rows)


def run():
    if not os.path.exists(DB_PATH):
        print(f"✗  Database not found at: {DB_PATH}")
        print("   Make sure you're running this from your project root folder.")
        return

    conn = sqlite3.connect(DB_PATH)

    columns_to_add = [
        ("deposit_paid",           "INTEGER NOT NULL DEFAULT 0"),
        ("deposit_paid_at",        "DATETIME"),
        ("deposit_payment_method", "TEXT"),
    ]

    any_added = False
    for col_name, col_def in columns_to_add:
        if column_exists(conn, "estimate_requests", col_name):
            print(f"   skipped  {col_name}  (already exists)")
        else:
            conn.execute(
                f"ALTER TABLE estimate_requests ADD COLUMN {col_name} {col_def}"
            )
            conn.commit()
            print(f"✓  added    {col_name}")
            any_added = True

    conn.close()

    if any_added:
        print("\nDone. Your database is up to date.")
    else:
        print("\nNothing to do — all columns already exist.")


if __name__ == "__main__":
    run()