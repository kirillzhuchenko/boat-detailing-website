"""
add_payment_columns.py
──────────────────────
Run this ONCE from your project root to add final-payment
tracking columns to your existing database.

Stop your Flask app first, run this, then restart.

Usage:
    python add_payment_columns.py
"""

import sqlite3
import os

DB_PATH = os.environ.get("DATABASE_URL", "instance/bluewater.db")

MOBILIZATION_FEE = 150.0   # keep in sync with your rates


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
        # How much of the final balance has been collected (deposit not counted here)
        ("amount_paid",      "REAL NOT NULL DEFAULT 0"),
        # unpaid | partial | paid_in_full
        ("payment_status",   "TEXT NOT NULL DEFAULT 'unpaid'"),
        # venmo | zelle | cash | card | check | other
        ("payment_method",   "TEXT"),
        # timestamp of most recent payment
        ("payment_date",     "DATETIME"),
        # free-form staff notes, e.g. "paid in two installments"
        ("payment_notes",    "TEXT"),
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
        print("You can now track payments with record_payment() in database.py.")
    else:
        print("\nNothing to do — all columns already exist.")


if __name__ == "__main__":
    run()