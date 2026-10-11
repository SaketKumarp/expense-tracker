
import sqlite3
from datetime import date, datetime
from pathlib import Path
from typing import Optional
from fastmcp import FastMCP

mcp = FastMCP(name="Personal Expense Tracker")

# Store the database beside this Python file.
DB_PATH = Path(__file__).resolve().parent / "expenses.db"

CATEGORIES = {
    "food",
    "travel",
    "shopping",
    "rent",
    "bills",
    "health",
    "education",
    "entertainment",
    "other",
}


def get_connection():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def initialize_database():
    with get_connection() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS expenses (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                amount_paise INTEGER NOT NULL CHECK (amount_paise > 0),
                category TEXT NOT NULL,
                note TEXT NOT NULL DEFAULT '',
                spent_at TEXT NOT NULL,
                created_at TEXT NOT NULL
            )
        """)


def validate_date(value: str) -> str:
    """Validate and normalize a date in YYYY-MM-DD format."""
    return date.fromisoformat(value).isoformat()


def validate_category(category: str) -> str:
    category = category.strip().lower()

    if category not in CATEGORIES:
        raise ValueError(
            f"Invalid category. Choose from: {', '.join(sorted(CATEGORIES))}"
        )

    return category


def row_to_dict(row):
    result = dict(row)
    result["amount"] = result.pop("amount_paise") / 100
    return result


initialize_database()


@mcp.tool
def add_expense(
    amount: float,
    category: str,
    note: str = "",
    spent_at: Optional[str] = None,
) -> dict:
    """Record an expense in Indian rupees.

    amount: Expense amount in rupees.
    category: food, travel, shopping, rent, bills, health,
              education, entertainment, or other.
    note: Optional description.
    spent_at: Optional date in YYYY-MM-DD format; defaults to today.
    """
    if amount <= 0 or amount > 10_000_000:
        raise ValueError("Amount must be greater than zero and reasonable.")

    category = validate_category(category)
    expense_date = validate_date(spent_at) if spent_at else date.today().isoformat()

    # Store money as integer paise to avoid floating-point storage errors.
    amount_paise = round(amount * 100)

    with get_connection() as conn:
        cursor = conn.execute(
            """
            INSERT INTO expenses
                (amount_paise, category, note, spent_at, created_at)
            VALUES (?, ?, ?, ?, ?)
            """,
            (
                amount_paise,
                category,
                note.strip(),
                expense_date,
                datetime.now().astimezone().isoformat(),
            ),
        )

        expense_id = cursor.lastrowid

    return {
        "success": True,
        "message": "Expense added successfully.",
        "expense_id": expense_id,
        "amount": amount_paise / 100,
        "currency": "INR",
        "category": category,
        "note": note.strip(),
        "spent_at": expense_date,
    }


@mcp.tool
def list_expenses(
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    category: Optional[str] = None,
    limit: int = 20,
) -> dict:
    """List expenses, optionally filtered by date and category.

    Dates use YYYY-MM-DD. Results are newest first.
    """
    if not 1 <= limit <= 100:
        raise ValueError("Limit must be between 1 and 100.")

    conditions = []
    params = []

    if start_date:
        conditions.append("spent_at >= ?")
        params.append(validate_date(start_date))

    if end_date:
        conditions.append("spent_at <= ?")
        params.append(validate_date(end_date))

    if start_date and end_date and start_date > end_date:
        raise ValueError("start_date must not be after end_date.")

    if category:
        conditions.append("category = ?")
        params.append(validate_category(category))

    where_clause = (
        "WHERE " + " AND ".join(conditions) if conditions else ""
    )

    with get_connection() as conn:
        rows = conn.execute(
            f"""
            SELECT id, amount_paise, category, note, spent_at, created_at
            FROM expenses
            {where_clause}
            ORDER BY spent_at DESC, id DESC
            LIMIT ?
            """,
            (*params, limit),
        ).fetchall()

    expenses = [row_to_dict(row) for row in rows]

    return {
        "count": len(expenses),
        "expenses": expenses,
        "currency": "INR",
    }


@mcp.tool
def get_monthly_summary(year_month: Optional[str] = None) -> dict:
    """Get total spending and category breakdown for a month.

    year_month must be YYYY-MM. Defaults to the current month.
    """
    if year_month is None:
        year_month = date.today().strftime("%Y-%m")

    try:
        datetime.strptime(year_month, "%Y-%m")
    except ValueError:
        raise ValueError("Use YYYY-MM format, for example 2026-10.")

    with get_connection() as conn:
        total = conn.execute(
            """
            SELECT COUNT(*) AS count,
                   COALESCE(SUM(amount_paise), 0) AS total_paise
            FROM expenses
            WHERE substr(spent_at, 1, 7) = ?
            """,
            (year_month,),
        ).fetchone()

        rows = conn.execute(
            """
            SELECT category,
                   SUM(amount_paise) AS total_paise,
                   COUNT(*) AS count
            FROM expenses
            WHERE substr(spent_at, 1, 7) = ?
            GROUP BY category
            ORDER BY total_paise DESC
            """,
            (year_month,),
        ).fetchall()

    return {
        "month": year_month,
        "total_spent": total["total_paise"] / 100,
        "expense_count": total["count"],
        "currency": "INR",
        "category_breakdown": [
            {
                "category": row["category"],
                "total": row["total_paise"] / 100,
                "count": row["count"],
            }
            for row in rows
        ],
    }


@mcp.tool
def find_expensive_categories(
    year_month: Optional[str] = None,
) -> dict:
    """Rank expense categories from highest to lowest spending."""
    summary = get_monthly_summary(year_month)

    categories = summary["category_breakdown"]

    return {
        "month": summary["month"],
        "highest_spending_category": categories[0] if categories else None,
        "ranked_categories": categories,
        "currency": "INR",
    }


@mcp.tool
def delete_expense(expense_id: int) -> dict:
    """Permanently delete an expense using its ID.

    Check the transaction with list_expenses before deleting it.
    """
    with get_connection() as conn:
        cursor = conn.execute(
            "DELETE FROM expenses WHERE id = ?",
            (expense_id,),
        )

        deleted = cursor.rowcount > 0

    if not deleted:
        return {
            "success": False,
            "message": f"No expense found with ID {expense_id}.",
        }

    return {
        "success": True,
        "message": f"Expense {expense_id} deleted successfully.",
    }


if __name__ == "__main__":
    mcp.run()
