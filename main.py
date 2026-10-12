 
import os
from datetime import date, datetime
from decimal import Decimal, ROUND_HALF_UP
from pathlib import Path
from typing import Optional

import psycopg
from psycopg.rows import dict_row
from dotenv import load_dotenv
from fastmcp import FastMCP
 

BASE_DIR = Path(__file__).resolve().parent
load_dotenv(BASE_DIR / ".env")

DATABASE_URL = os.getenv("DATABASE_URL")

if not DATABASE_URL:
    raise RuntimeError(
        "DATABASE_URL is missing. Add it to your .env file."
    )

mcp = FastMCP(name="Personal Expense Tracker")

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


# --------------------------------------------------
# Database connection
# --------------------------------------------------

def get_connection():
    return psycopg.connect(
        DATABASE_URL,
        row_factory=dict_row,
        connect_timeout=10,
    )


# --------------------------------------------------
# Validation and conversion helpers
# --------------------------------------------------

def validate_date(value: str) -> str:
    """Validate and normalize YYYY-MM-DD."""
    return date.fromisoformat(value).isoformat()


def validate_category(category: str) -> str:
    category = category.strip().lower()

    if category not in CATEGORIES:
        raise ValueError(
            f"Invalid category. Choose from: "
            f"{', '.join(sorted(CATEGORIES))}"
        )

    return category


def to_paise(amount: float) -> int:
    """Convert rupees to integer paise."""
    value = Decimal(str(amount))

    if not value.is_finite() or value <= 0 or value > 10_000_000:
        raise ValueError(
            "Amount must be greater than zero and reasonable."
        )

    paise = int(
        (value * 100).quantize(
            Decimal("1"),
            rounding=ROUND_HALF_UP,
        )
    )

    if paise <= 0:
        raise ValueError("Amount must be at least one paisa.")

    return paise


def row_to_dict(row: dict) -> dict:
    result = dict(row)
    result["amount"] = result.pop("amount_paise") / 100

    # Return dates as strings for MCP clients.
    if isinstance(result.get("spent_at"), date):
        result["spent_at"] = result["spent_at"].isoformat()

    if isinstance(result.get("created_at"), datetime):
        result["created_at"] = result["created_at"].isoformat()

    return result


def validate_month(year_month: str) -> str:
    try:
        datetime.strptime(year_month, "%Y-%m")
    except ValueError:
        raise ValueError(
            "Use YYYY-MM format, for example 2026-10."
        )

    return year_month


# --------------------------------------------------
# Tool 1: Add an expense
# --------------------------------------------------

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
    spent_at: Optional YYYY-MM-DD date; defaults to today.
    """

    amount_paise = to_paise(amount)
    category = validate_category(category)
    expense_date = (
        validate_date(spent_at)
        if spent_at
        else date.today().isoformat()
    )
    clean_note = note.strip()

    if len(clean_note) > 500:
        raise ValueError("Note cannot exceed 500 characters.")

    with get_connection() as conn:
        row = conn.execute(
            """
            INSERT INTO public.expenses
                (amount_paise, category, note, spent_at)
            VALUES (%s, %s, %s, %s)
            RETURNING id, amount_paise, category, note,
                      spent_at, created_at
            """,
            (
                amount_paise,
                category,
                clean_note,
                expense_date,
            ),
        ).fetchone()

    expense = row_to_dict(row)

    return {
        "success": True,
        "message": "Expense added successfully.",
        "expense_id": expense["id"],
        "amount": expense["amount"],
        "currency": "INR",
        "category": expense["category"],
        "note": expense["note"],
        "spent_at": expense["spent_at"],
    }


# --------------------------------------------------
# Tool 2: List expenses
# --------------------------------------------------

@mcp.tool
def list_expenses(
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    category: Optional[str] = None,
    limit: int = 20,
) -> dict:
    """List expenses, optionally filtered by date and category.

    Dates use YYYY-MM-DD. Results are newest first.
    Limit must be between 1 and 100.
    """

    if not 1 <= limit <= 100:
        raise ValueError("Limit must be between 1 and 100.")

    conditions = []
    params = []

    if start_date:
        start_date = validate_date(start_date)
        conditions.append("spent_at >= %s")
        params.append(start_date)

    if end_date:
        end_date = validate_date(end_date)
        conditions.append("spent_at <= %s")
        params.append(end_date)

    if start_date and end_date and start_date > end_date:
        raise ValueError("start_date must not be after end_date.")

    if category:
        conditions.append("category = %s")
        params.append(validate_category(category))

    where_clause = (
        "WHERE " + " AND ".join(conditions)
        if conditions
        else ""
    )

    # Only the internally constructed WHERE clause is interpolated.
    # All user-supplied values are passed as SQL parameters.
    with get_connection() as conn:
        rows = conn.execute(
            f"""
            SELECT id, amount_paise, category, note,
                   spent_at, created_at
            FROM public.expenses
            {where_clause}
            ORDER BY spent_at DESC, id DESC
            LIMIT %s
            """,
            (*params, limit),
        ).fetchall()

    expenses = [row_to_dict(row) for row in rows]

    return {
        "count": len(expenses),
        "expenses": expenses,
        "currency": "INR",
    }


# --------------------------------------------------
# Tool 3: Monthly summary
# --------------------------------------------------

@mcp.tool
def get_monthly_summary(
    year_month: Optional[str] = None,
) -> dict:
    """Get total spending and category breakdown for a month.

    year_month must be YYYY-MM. Defaults to the current month.
    """

    if year_month is None:
        year_month = date.today().strftime("%Y-%m")

    year_month = validate_month(year_month)
    month_start = f"{year_month}-01"

    with get_connection() as conn:
        total = conn.execute(
            """
            SELECT
                COUNT(*) AS count,
                COALESCE(SUM(amount_paise), 0) AS total_paise
            FROM public.expenses
            WHERE spent_at >= %s::date
              AND spent_at < (%s::date + INTERVAL '1 month')
            """,
            (month_start, month_start),
        ).fetchone()

        rows = conn.execute(
            """
            SELECT
                category,
                SUM(amount_paise) AS total_paise,
                COUNT(*) AS count
            FROM public.expenses
            WHERE spent_at >= %s::date
              AND spent_at < (%s::date + INTERVAL '1 month')
            GROUP BY category
            ORDER BY total_paise DESC
            """,
            (month_start, month_start),
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


# --------------------------------------------------
# Tool 4: Rank expensive categories
# --------------------------------------------------

@mcp.tool
def find_expensive_categories(
    year_month: Optional[str] = None,
) -> dict:
    """Rank expense categories from highest to lowest spending."""

    summary = get_monthly_summary(year_month)
    categories = summary["category_breakdown"]

    return {
        "month": summary["month"],
        "highest_spending_category": (
            categories[0] if categories else None
        ),
        "ranked_categories": categories,
        "currency": "INR",
    }


# --------------------------------------------------
# Tool 5: Delete an expense
# --------------------------------------------------

@mcp.tool
def delete_expense(expense_id: int) -> dict:
    """Permanently delete an expense using its ID.

    Check the transaction with list_expenses before deleting it.
    """

    if expense_id <= 0:
        raise ValueError("expense_id must be a positive integer.")

    with get_connection() as conn:
        row = conn.execute(
            """
            DELETE FROM public.expenses
            WHERE id = %s
            RETURNING id
            """,
            (expense_id,),
        ).fetchone()

    if row is None:
        return {
            "success": False,
            "message": f"No expense found with ID {expense_id}.",
        }

    return {
        "success": True,
        "message": f"Expense {expense_id} deleted successfully.",
    }


# --------------------------------------------------
# Start MCP server
# --------------------------------------------------

if __name__ == "__main__":
    mcp.run()
