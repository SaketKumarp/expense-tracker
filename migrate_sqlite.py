 
import sqlite3
import os
from pathlib import Path

import psycopg
from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent
SQLITE_PATH = BASE_DIR / "expenses.db"

load_dotenv(BASE_DIR / ".env")
DATABASE_URL = os.getenv("DATABASE_URL")

if not DATABASE_URL:
    raise RuntimeError("DATABASE_URL is missing from .env")

# Read existing SQLite records without modifying the source database.
sqlite_conn = sqlite3.connect(SQLITE_PATH)
sqlite_conn.row_factory = sqlite3.Row

try:
    expenses = sqlite_conn.execute("""
        SELECT id, amount_paise, category, note, spent_at, created_at
        FROM expenses
        ORDER BY id
    """).fetchall()
finally:
    sqlite_conn.close()

print(f"Found {len(expenses)} expenses in SQLite.")

# Copy the records to Supabase.
with psycopg.connect(DATABASE_URL) as pg_conn:
    with pg_conn.cursor() as cur:
        for expense in expenses:
            cur.execute(
                """
                INSERT INTO public.expenses
                    (id, amount_paise, category, note, spent_at, created_at)
                VALUES (%s, %s, %s, %s, %s, %s)
                ON CONFLICT (id) DO NOTHING
                """,
                (
                    expense["id"],
                    expense["amount_paise"],
                    expense["category"],
                    expense["note"],
                    expense["spent_at"],
                    expense["created_at"],
                ),
            )

        # Keep the auto-generated ID sequence ahead of existing IDs.
        cur.execute("""
            SELECT setval(
                pg_get_serial_sequence('public.expenses', 'id'),
                COALESCE(MAX(id), 1),
                MAX(id) IS NOT NULL
            )
            FROM public.expenses
        """)

        cur.execute("SELECT COUNT(*) FROM public.expenses")
        total_in_supabase = cur.fetchone()[0]

print("Migration finished.")
print(f"SQLite source records: {len(expenses)}")
print(f"Total records in Supabase: {total_in_supabase}")
print("SQLite database was not modified.")
 