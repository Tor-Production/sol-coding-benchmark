"""SQLite-backed inventory and reservation operations."""

import sqlite3
from contextlib import closing


class Conflict(Exception):
    """A request conflicts with existing stock or an idempotency key."""


class NotFound(Exception):
    """The requested item or reservation does not exist."""


_MAX_INTEGER = 2**63 - 1


def _name(value, label):
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{label} must be a nonblank string")
    return value.strip()


def _positive_integer(value, label):
    if type(value) is not int or not 0 < value <= _MAX_INTEGER:
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
        with closing(self._connect()) as connection:
            with connection:
                connection.execute("""
                    CREATE TABLE IF NOT EXISTS items (
                        sku TEXT PRIMARY KEY,
                        available INTEGER NOT NULL CHECK (available >= 0)
                    )
                """)
                connection.execute("""
                    CREATE TABLE IF NOT EXISTS reservations (
                        reservation_id INTEGER PRIMARY KEY,
                        idempotency_key TEXT NOT NULL UNIQUE,
                        sku TEXT NOT NULL REFERENCES items(sku),
                        quantity INTEGER NOT NULL CHECK (quantity > 0),
                        status TEXT NOT NULL CHECK (status IN ('active', 'released'))
                    )
                """)

    def _connect(self):
        connection = sqlite3.connect(self.db_path, timeout=30)
        connection.execute("PRAGMA foreign_keys = ON")
        return connection

    def add_item(self, sku, quantity):
        sku = _name(sku, "sku")
        quantity = _positive_integer(quantity, "quantity")
        with closing(self._connect()) as connection:
            with connection:
                connection.execute("BEGIN IMMEDIATE")
                row = connection.execute(
                    "SELECT available FROM items WHERE sku = ?", (sku,)
                ).fetchone()
                available = quantity + (row[0] if row else 0)
                if available > _MAX_INTEGER:
                    raise ValueError("available quantity exceeds SQLite integer range")
                if row:
                    connection.execute(
                        "UPDATE items SET available = ? WHERE sku = ?", (available, sku)
                    )
                else:
                    connection.execute(
                        "INSERT INTO items (sku, available) VALUES (?, ?)",
                        (sku, available),
                    )
        return {"sku": sku, "available": available}

    def get_item(self, sku):
        sku = _name(sku, "sku")
        with closing(self._connect()) as connection:
            row = connection.execute(
                "SELECT available FROM items WHERE sku = ?", (sku,)
            ).fetchone()
        if row is None:
            raise NotFound(f"item {sku!r} not found")
        return {"sku": sku, "available": row[0]}

    def reserve(self, key, sku, quantity):
        key = _name(key, "idempotency_key")
        sku = _name(sku, "sku")
        quantity = _positive_integer(quantity, "quantity")
        with closing(self._connect()) as connection:
            with connection:
                connection.execute("BEGIN IMMEDIATE")
                row = connection.execute(
                    "SELECT reservation_id, idempotency_key, sku, quantity, status "
                    "FROM reservations WHERE idempotency_key = ?", (key,)
                ).fetchone()
                if row is not None:
                    if row[2] != sku or row[3] != quantity:
                        raise Conflict("idempotency key already used for another request")
                    return _reservation(row)

                item = connection.execute(
                    "SELECT available FROM items WHERE sku = ?", (sku,)
                ).fetchone()
                if item is None:
                    raise NotFound(f"item {sku!r} not found")
                if item[0] < quantity:
                    raise Conflict("insufficient stock")
                connection.execute(
                    "UPDATE items SET available = available - ? WHERE sku = ?",
                    (quantity, sku),
                )
                cursor = connection.execute(
                    "INSERT INTO reservations (idempotency_key, sku, quantity, status) "
                    "VALUES (?, ?, ?, 'active')", (key, sku, quantity)
                )
                reservation_id = cursor.lastrowid
        return {
            "reservation_id": reservation_id,
            "idempotency_key": key,
            "sku": sku,
            "quantity": quantity,
            "status": "active",
        }

    def release(self, reservation_id):
        reservation_id = _positive_integer(reservation_id, "reservation_id")
        with closing(self._connect()) as connection:
            with connection:
                connection.execute("BEGIN IMMEDIATE")
                row = connection.execute(
                    "SELECT reservation_id, idempotency_key, sku, quantity, status "
                    "FROM reservations WHERE reservation_id = ?", (reservation_id,)
                ).fetchone()
                if row is None:
                    raise NotFound(f"reservation {reservation_id} not found")
                if row[4] == "active":
                    available = connection.execute(
                        "SELECT available FROM items WHERE sku = ?", (row[2],)
                    ).fetchone()[0]
                    if available + row[3] > _MAX_INTEGER:
                        raise ValueError("available quantity exceeds SQLite integer range")
                    connection.execute(
                        "UPDATE reservations SET status = 'released' "
                        "WHERE reservation_id = ?", (reservation_id,)
                    )
                    connection.execute(
                        "UPDATE items SET available = ? WHERE sku = ?",
                        (available + row[3], row[2]),
                    )
                return {**_reservation(row), "status": "released"}

    def report(self):
        with closing(self._connect()) as connection:
            with connection:
                connection.execute("BEGIN")
                items = [
                    {"sku": sku, "available": available}
                    for sku, available in connection.execute(
                        "SELECT sku, available FROM items ORDER BY sku"
                    )
                ]
                active = connection.execute(
                    "SELECT quantity FROM reservations WHERE status = 'active'"
                )
                count = 0
                units = 0
                for (quantity,) in active:
                    count += 1
                    units += quantity
        return {
            "items": items,
            "active_reservations": count,
            "reserved_units": units,
        }
