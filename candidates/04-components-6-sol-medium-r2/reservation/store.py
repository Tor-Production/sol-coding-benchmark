"""SQLite-backed inventory and reservation operations."""

import sqlite3
from contextlib import closing


class Conflict(Exception):
    pass


class NotFound(Exception):
    pass


def _name(value, label):
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{label} must be a nonblank string")
    return value.strip()


def _positive(value, label):
    if type(value) is not int or value <= 0:
        raise ValueError(f"{label} must be a positive integer")
    return value


def _reservation(row):
    return {
        "reservation_id": row[0],
        "idempotency_key": row[1],
        "sku": row[2],
        "quantity": row[3],
        "status": row[4],
    }


class Store:
    def __init__(self, db_path):
        self.db_path = db_path
        with closing(self._connect()) as conn:
            with conn:
                conn.execute("""CREATE TABLE IF NOT EXISTS items (
                    sku TEXT PRIMARY KEY,
                    available INTEGER NOT NULL CHECK (available >= 0)
                )""")
                conn.execute("""CREATE TABLE IF NOT EXISTS reservations (
                    reservation_id INTEGER PRIMARY KEY,
                    idempotency_key TEXT NOT NULL UNIQUE,
                    sku TEXT NOT NULL,
                    quantity INTEGER NOT NULL CHECK (quantity > 0),
                    status TEXT NOT NULL CHECK (status IN ('active', 'released'))
                )""")

    def _connect(self):
        return sqlite3.connect(self.db_path, timeout=30)

    def add_item(self, sku, quantity):
        sku = _name(sku, "sku")
        quantity = _positive(quantity, "quantity")
        with closing(self._connect()) as conn:
            with conn:
                conn.execute("BEGIN IMMEDIATE")
                conn.execute("""INSERT INTO items (sku, available) VALUES (?, ?)
                    ON CONFLICT(sku) DO UPDATE SET available = available + excluded.available""",
                    (sku, quantity))
                available = conn.execute(
                    "SELECT available FROM items WHERE sku = ?", (sku,)
                ).fetchone()[0]
        return {"sku": sku, "available": available}

    def get_item(self, sku):
        sku = _name(sku, "sku")
        with closing(self._connect()) as conn:
            row = conn.execute("SELECT available FROM items WHERE sku = ?", (sku,)).fetchone()
        if row is None:
            raise NotFound(f"item {sku!r} not found")
        return {"sku": sku, "available": row[0]}

    def reserve(self, key, sku, quantity):
        key = _name(key, "idempotency_key")
        sku = _name(sku, "sku")
        quantity = _positive(quantity, "quantity")
        with closing(self._connect()) as conn:
            with conn:
                conn.execute("BEGIN IMMEDIATE")
                row = conn.execute("""SELECT reservation_id, idempotency_key, sku,
                    quantity, status FROM reservations WHERE idempotency_key = ?""",
                    (key,)).fetchone()
                if row is not None:
                    if row[2] != sku or row[3] != quantity:
                        raise Conflict("idempotency key belongs to different parameters")
                    return _reservation(row)
                changed = conn.execute("""UPDATE items SET available = available - ?
                    WHERE sku = ? AND available >= ?""", (quantity, sku, quantity))
                if changed.rowcount == 0:
                    if conn.execute("SELECT 1 FROM items WHERE sku = ?", (sku,)).fetchone() is None:
                        raise NotFound(f"item {sku!r} not found")
                    raise Conflict("insufficient stock")
                cursor = conn.execute("""INSERT INTO reservations
                    (idempotency_key, sku, quantity, status) VALUES (?, ?, ?, 'active')""",
                    (key, sku, quantity))
                record = {
                    "reservation_id": cursor.lastrowid,
                    "idempotency_key": key,
                    "sku": sku,
                    "quantity": quantity,
                    "status": "active",
                }
        return record

    def release(self, reservation_id):
        reservation_id = _positive(reservation_id, "reservation_id")
        with closing(self._connect()) as conn:
            with conn:
                conn.execute("BEGIN IMMEDIATE")
                row = conn.execute("""SELECT reservation_id, idempotency_key, sku,
                    quantity, status FROM reservations WHERE reservation_id = ?""",
                    (reservation_id,)).fetchone()
                if row is None:
                    raise NotFound(f"reservation {reservation_id} not found")
                if row[4] == "active":
                    conn.execute("UPDATE reservations SET status = 'released' WHERE reservation_id = ?",
                                 (reservation_id,))
                    conn.execute("UPDATE items SET available = available + ? WHERE sku = ?",
                                 (row[3], row[2]))
                record = _reservation(row)
                record["status"] = "released"
        return record

    def report(self):
        with closing(self._connect()) as conn:
            with conn:
                conn.execute("BEGIN")
                items = [{"sku": sku, "available": available} for sku, available in
                         conn.execute("SELECT sku, available FROM items ORDER BY sku")]
                count, units = conn.execute("""SELECT COUNT(*), COALESCE(SUM(quantity), 0)
                    FROM reservations WHERE status = 'active'""").fetchone()
        return {"items": items, "active_reservations": count, "reserved_units": units}
