"""SQLite-backed inventory and idempotent reservations."""

import sqlite3
from contextlib import closing, contextmanager


class Conflict(Exception):
    pass


class NotFound(Exception):
    pass


def _name(value, label):
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{label} must be a nonblank string")
    return value.strip()


def _positive_integer(value, label):
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise ValueError(f"{label} must be a positive integer")
    if value > 2**63 - 1:
        raise ValueError(f"{label} exceeds SQLite's integer range")
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
        with self._transaction(write=True) as connection:
            connection.execute(
                "CREATE TABLE IF NOT EXISTS items ("
                "sku TEXT PRIMARY KEY, available INTEGER NOT NULL CHECK (available >= 0))"
            )
            connection.execute(
                "CREATE TABLE IF NOT EXISTS reservations ("
                "reservation_id INTEGER PRIMARY KEY, "
                "idempotency_key TEXT NOT NULL UNIQUE, "
                "sku TEXT NOT NULL REFERENCES items(sku), "
                "quantity INTEGER NOT NULL CHECK (quantity > 0), "
                "status TEXT NOT NULL CHECK (status IN ('active', 'released')))"
            )

    @contextmanager
    def _transaction(self, write=False):
        with closing(sqlite3.connect(self.db_path, timeout=30, isolation_level=None)) as connection:
            connection.execute("PRAGMA busy_timeout = 30000")
            connection.execute("PRAGMA foreign_keys = ON")
            connection.execute("BEGIN IMMEDIATE" if write else "BEGIN")
            try:
                yield connection
            except BaseException:
                connection.rollback()
                raise
            else:
                connection.commit()

    def add_item(self, sku, quantity):
        sku = _name(sku, "sku")
        quantity = _positive_integer(quantity, "quantity")
        with self._transaction(write=True) as connection:
            row = connection.execute(
                "SELECT available FROM items WHERE sku = ?", (sku,)
            ).fetchone()
            available = quantity + (row[0] if row else 0)
            active_units = sum(
                reserved[0]
                for reserved in connection.execute(
                    "SELECT quantity FROM reservations WHERE sku = ? AND status = 'active'",
                    (sku,),
                )
            )
            if available + active_units > 2**63 - 1:
                raise ValueError("available quantity exceeds SQLite's integer range")
            if row:
                connection.execute(
                    "UPDATE items SET available = ? WHERE sku = ?", (available, sku)
                )
            else:
                connection.execute(
                    "INSERT INTO items (sku, available) VALUES (?, ?)", (sku, available)
                )
            return {"sku": sku, "available": available}

    def get_item(self, sku):
        sku = _name(sku, "sku")
        with self._transaction() as connection:
            row = connection.execute(
                "SELECT available FROM items WHERE sku = ?", (sku,)
            ).fetchone()
            if row is None:
                raise NotFound(f"SKU not found: {sku}")
            return {"sku": sku, "available": row[0]}

    def reserve(self, key, sku, quantity):
        key = _name(key, "idempotency_key")
        sku = _name(sku, "sku")
        quantity = _positive_integer(quantity, "quantity")
        with self._transaction(write=True) as connection:
            row = connection.execute(
                "SELECT reservation_id, idempotency_key, sku, quantity, status "
                "FROM reservations WHERE idempotency_key = ?", (key,)
            ).fetchone()
            if row is not None:
                if row[2] != sku or row[3] != quantity:
                    raise Conflict("idempotency key already used with different parameters")
                return {**_reservation(row), "status": "active"}

            stock = connection.execute(
                "SELECT available FROM items WHERE sku = ?", (sku,)
            ).fetchone()
            if stock is None:
                raise NotFound(f"SKU not found: {sku}")
            if stock[0] < quantity:
                raise Conflict(f"insufficient stock for SKU: {sku}")

            connection.execute(
                "UPDATE items SET available = available - ? WHERE sku = ?",
                (quantity, sku),
            )
            cursor = connection.execute(
                "INSERT INTO reservations (idempotency_key, sku, quantity, status) "
                "VALUES (?, ?, ?, 'active')", (key, sku, quantity)
            )
            return {
                "reservation_id": cursor.lastrowid,
                "idempotency_key": key,
                "sku": sku,
                "quantity": quantity,
                "status": "active",
            }

    def release(self, reservation_id):
        reservation_id = _positive_integer(reservation_id, "reservation_id")
        with self._transaction(write=True) as connection:
            row = connection.execute(
                "SELECT reservation_id, idempotency_key, sku, quantity, status "
                "FROM reservations WHERE reservation_id = ?", (reservation_id,)
            ).fetchone()
            if row is None:
                raise NotFound(f"reservation not found: {reservation_id}")
            if row[4] == "active":
                connection.execute(
                    "UPDATE reservations SET status = 'released' WHERE reservation_id = ?",
                    (reservation_id,),
                )
                connection.execute(
                    "UPDATE items SET available = available + ? WHERE sku = ?",
                    (row[3], row[2]),
                )
            return {**_reservation(row), "status": "released"}

    def report(self):
        with self._transaction() as connection:
            items = [
                {"sku": sku, "available": available}
                for sku, available in connection.execute(
                    "SELECT sku, available FROM items ORDER BY sku"
                )
            ]
            quantities = [
                row[0] for row in connection.execute(
                    "SELECT quantity FROM reservations WHERE status = 'active'"
                )
            ]
            return {
                "items": items,
                "active_reservations": len(quantities),
                "reserved_units": sum(quantities),
            }
