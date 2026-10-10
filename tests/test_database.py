from database import connect_db, ensure_database, init_database, seed_development_data


def test_init_database_creates_tables(tmp_path):
    db_path = tmp_path / "ridewise_test.db"
    conn = connect_db(str(db_path))
    init_database(conn)
    tables = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
    ).fetchall()
    names = {row[0] for row in tables}
    assert {
        "customers",
        "vehicles",
        "bookings",
        "payments",
        "damage_reports",
        "customer_memory",
    }.issubset(names)
    conn.close()


def test_seed_development_data_inserts_records(tmp_path):
    db_path = tmp_path / "ridewise_seed.db"
    conn = connect_db(str(db_path))
    init_database(conn)
    seed_development_data(conn)
    customer_count = conn.execute("SELECT COUNT(*) FROM customers").fetchone()[0]
    booking_count = conn.execute("SELECT COUNT(*) FROM bookings").fetchone()[0]
    assert customer_count >= 3
    assert booking_count >= 3
    conn.close()
