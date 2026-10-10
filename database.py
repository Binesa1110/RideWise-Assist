import sqlite3
from pathlib import Path
from typing import Any

from access_control import DEFAULT_ACCESS_CONTROL, DevelopmentUser
from config import BASE_DIR, DEFAULT_DB_PATH, get_database_path


def connect_db(db_path: str | None = None):
    path = db_path or get_database_path()
    db_file = Path(path)
    db_file.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(str(db_file))
    connection.row_factory = sqlite3.Row
    return connection


def init_database(connection: sqlite3.Connection | None = None, db_path: str | None = None):
    conn = connection or connect_db(db_path)
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS customers (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            email TEXT NOT NULL,
            phone TEXT,
            role TEXT NOT NULL DEFAULT 'customer'
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS vehicles (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            make TEXT NOT NULL,
            model TEXT NOT NULL,
            registration TEXT NOT NULL UNIQUE,
            type TEXT NOT NULL
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS bookings (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            customer_id INTEGER NOT NULL,
            vehicle_id INTEGER NOT NULL,
            booking_ref TEXT NOT NULL UNIQUE,
            start_time TEXT NOT NULL,
            end_time TEXT NOT NULL,
            status TEXT NOT NULL,
            total_amount REAL NOT NULL DEFAULT 0,
            FOREIGN KEY(customer_id) REFERENCES customers(id),
            FOREIGN KEY(vehicle_id) REFERENCES vehicles(id)
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS payments (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            booking_id INTEGER NOT NULL,
            amount REAL NOT NULL,
            currency TEXT NOT NULL DEFAULT 'INR',
            status TEXT NOT NULL,
            paid_at TEXT,
            method TEXT,
            FOREIGN KEY(booking_id) REFERENCES bookings(id)
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS damage_reports (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            booking_id INTEGER NOT NULL,
            reported_by INTEGER NOT NULL,
            description TEXT NOT NULL,
            estimated_cost REAL NOT NULL DEFAULT 0,
            status TEXT NOT NULL DEFAULT 'open',
            created_at TEXT NOT NULL,
            FOREIGN KEY(booking_id) REFERENCES bookings(id),
            FOREIGN KEY(reported_by) REFERENCES customers(id)
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS customer_memory (
            memory_id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            issue_category TEXT NOT NULL,
            issue_keywords TEXT NOT NULL,
            resolution_keywords TEXT NOT NULL DEFAULT '[]',
            issue_status TEXT NOT NULL DEFAULT 'open'
                CHECK (issue_status IN ('open', 'in_progress', 'resolved')),
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            FOREIGN KEY(user_id) REFERENCES customers(id),
            UNIQUE(user_id, issue_category, issue_keywords)
        )
        """
    )
    conn.commit()
    if connection is None:
        conn.close()


def seed_development_data(connection: sqlite3.Connection):
    customer_count = connection.execute("SELECT COUNT(*) FROM customers").fetchone()[0]
    if customer_count:
        return

    connection.execute(
        "INSERT INTO customers (name, email, phone, role) VALUES (?, ?, ?, ?)",
        ("Aisha Patel", "aisha@example.com", "9876543210", "customer"),
    )
    connection.execute(
        "INSERT INTO customers (name, email, phone, role) VALUES (?, ?, ?, ?)",
        ("Rohan Shah", "rohan@example.com", "9988776655", "support"),
    )
    connection.execute(
        "INSERT INTO customers (name, email, phone, role) VALUES (?, ?, ?, ?)",
        ("Admin User", "admin@example.com", "9123456789", "admin"),
    )

    connection.execute(
        "INSERT INTO vehicles (make, model, registration, type) VALUES (?, ?, ?, ?)",
        ("Tesla", "Model 3", "MH12AB1234", "EV"),
    )
    connection.execute(
        "INSERT INTO vehicles (make, model, registration, type) VALUES (?, ?, ?, ?)",
        ("Maruti", "Swift", "MH14CD5678", "Petrol"),
    )

    connection.execute(
        "INSERT INTO bookings (customer_id, vehicle_id, booking_ref, start_time, end_time, status, total_amount) VALUES (?, ?, ?, ?, ?, ?, ?)",
        (1, 1, "RW-1001", "2025-01-10T10:00:00", "2025-01-12T10:00:00", "completed", 1800.0),
    )
    connection.execute(
        "INSERT INTO bookings (customer_id, vehicle_id, booking_ref, start_time, end_time, status, total_amount) VALUES (?, ?, ?, ?, ?, ?, ?)",
        (1, 2, "RW-1002", "2025-02-15T09:00:00", "2025-02-18T09:00:00", "active", 2400.0),
    )
    connection.execute(
        "INSERT INTO bookings (customer_id, vehicle_id, booking_ref, start_time, end_time, status, total_amount) VALUES (?, ?, ?, ?, ?, ?, ?)",
        (2, 1, "RW-2001", "2025-01-20T08:00:00", "2025-01-23T08:00:00", "completed", 2100.0),
    )

    connection.execute(
        "INSERT INTO payments (booking_id, amount, currency, status, paid_at, method) VALUES (?, ?, ?, ?, ?, ?)",
        (1, 1800.0, "INR", "paid", "2025-01-08T12:00:00", "UPI"),
    )
    connection.execute(
        "INSERT INTO payments (booking_id, amount, currency, status, paid_at, method) VALUES (?, ?, ?, ?, ?, ?)",
        (2, 2400.0, "INR", "pending", "2025-02-10T18:00:00", "Card"),
    )

    connection.execute(
        "INSERT INTO damage_reports (booking_id, reported_by, description, estimated_cost, status, created_at) VALUES (?, ?, ?, ?, ?, ?)",
        (1, 1, "Minor scratch on rear bumper.", 4000.0, "open", "2025-01-12T15:30:00"),
    )

    connection.commit()


def ensure_database():
    path = Path(get_database_path())
    if not path.exists():
        connection = connect_db(str(path))
        init_database(connection)
        seed_development_data(connection)
        connection.close()
        return path
    connection = connect_db(str(path))
    init_database(connection)
    seed_development_data(connection)
    connection.close()
    return path


def get_customer_by_id(customer_id: int):
    connection = connect_db()
    row = connection.execute("SELECT * FROM customers WHERE id = ?", (customer_id,)).fetchone()
    connection.close()
    return dict(row) if row else None


def get_customer_bookings(customer_id: int):
    connection = connect_db()
    rows = connection.execute(
        """
        SELECT b.*, v.make, v.model, v.registration, c.name AS customer_name
        FROM bookings b
        JOIN vehicles v ON v.id = b.vehicle_id
        JOIN customers c ON c.id = b.customer_id
        WHERE b.customer_id = ?
        ORDER BY b.start_time DESC
        """,
        (customer_id,),
    ).fetchall()
    connection.close()
    return [dict(row) for row in rows]


def get_booking_by_id(booking_id: int):
    connection = connect_db()
    row = connection.execute(
        """
        SELECT b.*, c.name AS customer_name, v.make, v.model, v.registration, v.type AS vehicle_type
        FROM bookings b
        JOIN customers c ON c.id = b.customer_id
        JOIN vehicles v ON v.id = b.vehicle_id
        WHERE b.id = ?
        """,
        (booking_id,),
    ).fetchone()
    connection.close()
    return dict(row) if row else None


def get_booking_by_ref(booking_ref: str):
    connection = connect_db()
    row = connection.execute(
        """
        SELECT b.*, c.name AS customer_name, v.make, v.model, v.registration, v.type AS vehicle_type
        FROM bookings b
        JOIN customers c ON c.id = b.customer_id
        JOIN vehicles v ON v.id = b.vehicle_id
        WHERE b.booking_ref = ?
        """,
        (booking_ref,),
    ).fetchone()
    connection.close()
    return dict(row) if row else None


def get_payment_records_for_booking(booking_id: int):
    connection = connect_db()
    rows = connection.execute(
        "SELECT * FROM payments WHERE booking_id = ? ORDER BY paid_at DESC",
        (booking_id,),
    ).fetchall()
    connection.close()
    return [dict(row) for row in rows]


def get_damage_reports_for_booking(booking_id: int):
    connection = connect_db()
    rows = connection.execute(
        "SELECT * FROM damage_reports WHERE booking_id = ? ORDER BY created_at DESC",
        (booking_id,),
    ).fetchall()
    connection.close()
    return [dict(row) for row in rows]


def get_agent_booking_by_ref(actor: DevelopmentUser | None, booking_ref: str):
    """Look up only a booking the verified actor is authorized to see."""
    user = DEFAULT_ACCESS_CONTROL.resolve_verified_user(actor)
    if not user:
        raise PermissionError("A verified development user is required.")
    can_read_all = DEFAULT_ACCESS_CONTROL.authorize(user, "booking")
    if not can_read_all and not DEFAULT_ACCESS_CONTROL.authorize(
        user, "booking", user.user_id
    ):
        raise PermissionError("Booking access is not permitted.")

    connection = connect_db()
    try:
        row = connection.execute(
            """
            SELECT b.booking_ref, b.start_time, b.end_time, b.status, b.total_amount,
                   v.make AS vehicle_make, v.model AS vehicle_model, v.type AS vehicle_type
            FROM bookings b
            LEFT JOIN vehicles v ON v.id = b.vehicle_id
            WHERE b.booking_ref = ?
              AND (? = 1 OR b.customer_id = ?)
            """,
            (booking_ref, int(can_read_all), user.user_id),
        ).fetchone()
        return dict(row) if row else None
    finally:
        connection.close()


def get_agent_damage_reports(actor: DevelopmentUser | None, booking_ref: str):
    """Return damage reports only when the actor can access the associated booking."""
    user = DEFAULT_ACCESS_CONTROL.resolve_verified_user(actor)
    if not user:
        raise PermissionError("A verified development user is required.")
    can_read_all = DEFAULT_ACCESS_CONTROL.authorize(user, "damage")
    if not can_read_all and not DEFAULT_ACCESS_CONTROL.authorize(
        user, "damage", user.user_id
    ):
        raise PermissionError("Damage report access is not permitted.")

    connection = connect_db()
    try:
        rows = connection.execute(
            """
            SELECT d.status, d.description, d.estimated_cost, d.created_at
            FROM damage_reports d
            JOIN bookings b ON b.id = d.booking_id
            WHERE b.booking_ref = ?
              AND (? = 1 OR b.customer_id = ?)
            ORDER BY d.created_at DESC
            LIMIT 100
            """,
            (booking_ref, int(can_read_all), user.user_id),
        ).fetchall()
        return [dict(row) for row in rows]
    finally:
        connection.close()


if __name__ == "__main__":
    ensure_database()
    print(f"Database ready at {get_database_path()}")
