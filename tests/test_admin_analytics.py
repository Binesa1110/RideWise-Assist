import sqlite3

import pytest

from access_control import DevelopmentUser
from analytics import AdminAuthorizationError, get_admin_analytics
from database import connect_db, init_database


def _admin():
    return DevelopmentUser(3, "admin", "admin")


def test_non_admin_roles_are_rejected_before_analytics_read(tmp_path):
    db_path = tmp_path / "analytics.db"
    for role, user_id in (("customer", 1), ("support", 2)):
        with pytest.raises(AdminAuthorizationError):
            get_admin_analytics(DevelopmentUser(user_id, role, role), str(db_path))
    assert not db_path.exists()


def test_admin_role_is_authorized_for_analytics():
    from access_control import AccessControl

    assert AccessControl().authorize(_admin(), "analytics") is True


def test_empty_schema_returns_zero_metrics_and_unavailable_trends(tmp_path):
    db_path = tmp_path / "empty.db"
    connection = connect_db(str(db_path))
    init_database(connection)
    connection.close()

    analytics = get_admin_analytics(_admin(), str(db_path))

    assert analytics["bookings"]["total"] == 0
    assert analytics["bookings"]["active"] == 0
    assert analytics["bookings"]["status_distribution"] == []
    assert analytics["bookings"]["cancellation_trend"] is None
    assert analytics["payments"]["by_status"] == []
    assert analytics["payments"]["monthly"] == []
    assert analytics["damage_reports"]["total"] == 0


def test_missing_tables_are_reported_without_crashing(tmp_path):
    analytics = get_admin_analytics(_admin(), str(tmp_path / "missing.db"))

    assert analytics["coverage"] == {
        "bookings_table_available": False,
        "payments_table_available": False,
        "damage_reports_table_available": False,
    }
    assert analytics["bookings"]["total"] is None
    assert analytics["payments"]["monthly"] is None
    assert analytics["damage_reports"]["total"] is None


def test_metrics_match_direct_sql_and_do_not_count_pending_as_paid(tmp_path):
    db_path = tmp_path / "records.db"
    connection = connect_db(str(db_path))
    init_database(connection)
    connection.execute(
        "INSERT INTO customers (name, email, role) VALUES (?, ?, ?)",
        ("Private Customer", "private@example.test", "customer"),
    )
    connection.execute(
        "INSERT INTO vehicles (make, model, registration, type) VALUES (?, ?, ?, ?)",
        ("Make", "Model", "TEST-1", "EV"),
    )
    for ref, status in (("B-1", "active"), ("B-2", "completed"), ("B-3", "cancelled"), ("B-4", "other")):
        connection.execute(
            """
            INSERT INTO bookings
                (customer_id, vehicle_id, booking_ref, start_time, end_time, status, total_amount)
            VALUES (1, 1, ?, '2026-01-01', '2026-01-02', ?, 100)
            """,
            (ref, status),
        )
    connection.execute(
        "INSERT INTO payments (booking_id, amount, currency, status, paid_at) VALUES (1, 100, 'INR', 'paid', '2026-01-03T10:00:00')"
    )
    connection.execute(
        "INSERT INTO payments (booking_id, amount, currency, status, paid_at) VALUES (2, 900, 'INR', 'pending', '2026-01-04T10:00:00')"
    )
    connection.execute(
        """
        INSERT INTO damage_reports
            (booking_id, reported_by, description, estimated_cost, status, created_at)
        VALUES (1, 1, 'Private description', 12, 'open', '2026-01-05')
        """
    )
    connection.commit()
    direct_booking_total = connection.execute("SELECT COUNT(*) FROM bookings").fetchone()[0]
    direct_paid_total = connection.execute(
        "SELECT SUM(amount) FROM payments WHERE LOWER(status) = ?",
        ("paid",),
    ).fetchone()[0]
    connection.close()

    analytics = get_admin_analytics(_admin(), str(db_path))

    assert analytics["bookings"]["total"] == direct_booking_total == 4
    assert analytics["bookings"]["active"] == 1
    assert analytics["bookings"]["completed"] == 1
    assert analytics["bookings"]["cancelled"] == 1
    assert {row["status"]: row["count"] for row in analytics["bookings"]["status_distribution"]} == {
        "active": 1,
        "cancelled": 1,
        "completed": 1,
        "other": 1,
    }
    assert analytics["payments"]["paid_totals"][0]["amount"] == direct_paid_total == 100
    assert sum(row["amount"] for row in analytics["payments"]["by_status"]) == 1000
    assert analytics["payments"]["monthly"][0]["month"] == "2026-01"
    assert analytics["damage_reports"]["by_status"] == [{"status": "open", "count": 1}]
    assert "private@example.test" not in repr(analytics)
    assert "Private description" not in repr(analytics)


def test_partial_schema_reports_missing_data_without_crashing(tmp_path):
    db_path = tmp_path / "partial.db"
    connection = sqlite3.connect(db_path)
    connection.execute("CREATE TABLE bookings (id INTEGER PRIMARY KEY)")
    connection.execute("CREATE TABLE payments (id INTEGER PRIMARY KEY, status TEXT)")
    connection.commit()
    connection.close()

    analytics = get_admin_analytics(_admin(), str(db_path))

    assert analytics["bookings"]["total"] == 0
    assert analytics["bookings"]["active"] is None
    assert analytics["payments"]["by_status"] == []
    assert analytics["payments"]["monthly"] is None
