from __future__ import annotations

import sqlite3
from typing import Any

from access_control import DEFAULT_ACCESS_CONTROL, DevelopmentUser
from database import connect_db


class AdminAuthorizationError(PermissionError):
    """Raised when a non-administrator requests admin analytics."""


def _table_columns(connection: sqlite3.Connection, table: str) -> set[str]:
    return {
        row["name"]
        for row in connection.execute(f'PRAGMA table_info("{table}")').fetchall()
    }


def _rows_as_dicts(rows: list[sqlite3.Row]) -> list[dict[str, Any]]:
    return [dict(row) for row in rows]


def _booking_metrics(connection: sqlite3.Connection, tables: set[str]) -> dict[str, Any]:
    if "bookings" not in tables:
        return {
            "total": None,
            "active": None,
            "completed": None,
            "cancelled": None,
            "status_distribution": [],
            "cancellation_trend": None,
            "cancellation_trend_note": "Bookings table is not available.",
        }

    columns = _table_columns(connection, "bookings")
    total = connection.execute("SELECT COUNT(*) FROM bookings").fetchone()[0]
    distribution: list[dict[str, Any]] = []
    active = completed = cancelled = None
    if "status" in columns:
        distribution = _rows_as_dicts(
            connection.execute(
                """
                SELECT COALESCE(status, '(missing)') AS status, COUNT(*) AS count
                FROM bookings
                GROUP BY status
                ORDER BY status
                """
            ).fetchall()
        )
        counts = connection.execute(
            """
            SELECT
                SUM(CASE WHEN LOWER(status) = ? THEN 1 ELSE 0 END) AS active,
                SUM(CASE WHEN LOWER(status) = ? THEN 1 ELSE 0 END) AS completed,
                SUM(CASE WHEN LOWER(status) IN (?, ?) THEN 1 ELSE 0 END) AS cancelled
            FROM bookings
            """,
            ("active", "completed", "cancelled", "canceled"),
        ).fetchone()
        active, completed, cancelled = (counts[name] or 0 for name in ("active", "completed", "cancelled"))

    cancellation_trend = None
    if "status" in columns and "cancelled_at" in columns:
        cancellation_trend = _rows_as_dicts(
            connection.execute(
                """
                SELECT strftime('%Y-%m', cancelled_at) AS month, COUNT(*) AS count
                FROM bookings
                WHERE LOWER(status) IN (?, ?)
                  AND cancelled_at IS NOT NULL
                  AND strftime('%Y-%m', cancelled_at) IS NOT NULL
                GROUP BY strftime('%Y-%m', cancelled_at)
                ORDER BY month
                """,
                ("cancelled", "canceled"),
            ).fetchall()
        )
        cancellation_note = "Grouped by recorded cancellation date; rows without a valid date are omitted."
    else:
        cancellation_note = (
            "Unavailable: the current bookings schema has no cancellation timestamp."
        )

    return {
        "total": total,
        "active": active,
        "completed": completed,
        "cancelled": cancelled,
        "status_distribution": distribution,
        "cancellation_trend": cancellation_trend,
        "cancellation_trend_note": cancellation_note,
    }


def _payment_metrics(connection: sqlite3.Connection, tables: set[str]) -> dict[str, Any]:
    if "payments" not in tables:
        return {
            "by_status": [],
            "monthly": None,
            "monthly_note": "Payments table is not available.",
            "paid_totals": [],
        }

    columns = _table_columns(connection, "payments")
    if "status" not in columns:
        return {
            "by_status": [],
            "monthly": None,
            "monthly_note": "Unavailable: payment status column is missing.",
            "paid_totals": [],
        }

    currency_expr = "currency" if "currency" in columns else "'Unknown' AS currency"
    amount_expr = "SUM(amount)" if "amount" in columns else "NULL"
    by_status = _rows_as_dicts(
        connection.execute(
            f"""
            SELECT status, {currency_expr}, COUNT(*) AS count, {amount_expr} AS amount
            FROM payments
            GROUP BY status, currency
            ORDER BY status, currency
            """
        ).fetchall()
    )

    paid_totals = (
        _rows_as_dicts(
            connection.execute(
                f"""
                SELECT {currency_expr}, COUNT(*) AS count, SUM(amount) AS amount
                FROM payments
                WHERE LOWER(status) = ?
                GROUP BY currency
                ORDER BY currency
                """,
                ("paid",),
            ).fetchall()
        )
        if "amount" in columns
        else []
    )

    monthly = None
    monthly_note = "Unavailable: paid_at date column is missing."
    if "paid_at" in columns:
        monthly = _rows_as_dicts(
            connection.execute(
                f"""
                SELECT strftime('%Y-%m', paid_at) AS month, status,
                       {currency_expr}, COUNT(*) AS count, {amount_expr} AS amount
                FROM payments
                WHERE paid_at IS NOT NULL
                  AND strftime('%Y-%m', paid_at) IS NOT NULL
                GROUP BY strftime('%Y-%m', paid_at), status, currency
                ORDER BY month, status, currency
                """
            ).fetchall()
        )
        invalid_date_count = connection.execute(
            """
            SELECT COUNT(*) FROM payments
            WHERE paid_at IS NOT NULL AND strftime('%Y-%m', paid_at) IS NULL
            """
        ).fetchone()[0]
        monthly_note = (
            "Grouped by payment date, status, and currency; "
            f"{invalid_date_count} record(s) with invalid dates are omitted."
        )

    return {
        "by_status": by_status,
        "monthly": monthly,
        "monthly_note": monthly_note,
        "paid_totals": paid_totals,
        "revenue_note": (
            "Amounts are grouped by recorded payment status. Only status 'paid' "
            "is included in paid totals; this is not net revenue because refunds "
            "and chargebacks are not modeled here."
        ),
    }


def _damage_metrics(connection: sqlite3.Connection, tables: set[str]) -> dict[str, Any]:
    if "damage_reports" not in tables:
        return {"total": None, "by_status": []}

    columns = _table_columns(connection, "damage_reports")
    total = connection.execute("SELECT COUNT(*) FROM damage_reports").fetchone()[0]
    by_status = (
        _rows_as_dicts(
            connection.execute(
                """
                SELECT COALESCE(status, '(missing)') AS status, COUNT(*) AS count
                FROM damage_reports
                GROUP BY status
                ORDER BY status
                """
            ).fetchall()
        )
        if "status" in columns
        else []
    )
    return {"total": total, "by_status": by_status}


def get_admin_analytics(
    actor: DevelopmentUser | None, db_path: str | None = None
) -> dict[str, Any]:
    """Return aggregate-only metrics; authorization is enforced before DB access."""
    if not DEFAULT_ACCESS_CONTROL.authorize(actor, "analytics"):
        raise AdminAuthorizationError("Administrator access is required.")

    connection = connect_db(db_path)
    try:
        tables = {
            row["name"]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = ?",
                ("table",),
            ).fetchall()
        }
        return {
            "bookings": _booking_metrics(connection, tables),
            "payments": _payment_metrics(connection, tables),
            "damage_reports": _damage_metrics(connection, tables),
            "coverage": {
                "bookings_table_available": "bookings" in tables,
                "payments_table_available": "payments" in tables,
                "damage_reports_table_available": "damage_reports" in tables,
            },
        }
    finally:
        connection.close()
