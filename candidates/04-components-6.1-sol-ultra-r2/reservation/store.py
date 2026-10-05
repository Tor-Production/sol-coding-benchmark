"""Transactional inventory and reservations backed by SQLite."""

import sqlite3
from contextlib import closing, contextmanager


class Conflict(Exception):
    """A reservation conflicts with stock or an existing idempotency key."""


class NotFound(Exception):
    """The requested item or reservation does not exist."""


def _text(value, name):
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} must be a nonblank string")
    return value.strip()


def _positive_integer(value, name):
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise ValueError(f"{name} must be a positive integer")
    return value


def _item_record(row):
    return {"sku": row["sku"], "available": int(row["available"])}


def _reservation_record(row):
    return {
        "reservation_id": row["reservation_id"],
        "idempotency_key": row["idempotency_key"],
        "sku": row["sku"],
        "quantity": int(row["quantity"]),
        "status": row["status"],
    }


class Store:
    def __init__(self, db_path):
        self.db_path = db_path
        with self._transaction(write=True) as connection:
            # Decimal text preserves Python integers beyond SQLite's 64-bit
            # range. Arithmetic happens while holding the write transaction.
            connection.execute("""
                CREATE TABLE IF NOT EXISTS items (
                    sku TEXT PRIMARY KEY NOT NULL,
                    available TEXT NOT NULL
                )
            """)
            connection.execute("""
                CREATE TABLE IF NOT EXISTS reservations (
                    reservation_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    idempotency_key TEXT UNIQUE NOT NULL,
                    sku TEXT NOT NULL REFERENCES items(sku),
                    quantity TEXT NOT NULL,
                    status TEXT NOT NULL CHECK (status IN ('active', 'released'))
                )
            """)

    @contextmanager
    def _transaction(self, write=False):
        # sqlite3's connection context manager commits/rolls back but does not
        # close the connection; closing() ensures no handles escape a call.
        with closing(sqlite3.connect(self.db_path, timeout=30, isolation_level=None)) as connection:
            connection.row_factory = sqlite3.Row
            connection.execute("PRAGMA foreign_keys = ON")
            with connection:
                connection.execute("BEGIN IMMEDIATE" if write else "BEGIN")
                yield connection

    def add_item(self, sku, quantity):
        sku = _text(sku, "sku")
        quantity = _positive_integer(quantity, "quantity")
        with self._transaction(write=True) as connection:
            row = connection.execute("SELECT available FROM items WHERE sku = ?", (sku,)).fetchone()
            available = quantity if row is None else int(row["available"]) + quantity
            if row is None:
                connection.execute("INSERT INTO items (sku, available) VALUES (?, ?)", (sku, str(available)))
            else:
                connection.execute("UPDATE items SET available = ? WHERE sku = ?", (str(available), sku))
            return {"sku": sku, "available": available}

    def get_item(self, sku):
        sku = _text(sku, "sku")
        with self._transaction() as connection:
            row = connection.execute("SELECT sku, available FROM items WHERE sku = ?", (sku,)).fetchone()
            if row is None:
                raise NotFound(f"Item not found: {sku}")
            return _item_record(row)

    def reserve(self, key, sku, quantity):
        key = _text(key, "idempotency_key")
        sku = _text(sku, "sku")
        quantity = _positive_integer(quantity, "quantity")
        with self._transaction(write=True) as connection:
            existing = connection.execute(
                "SELECT * FROM reservations WHERE idempotency_key = ?", (key,)
            ).fetchone()
            if existing is not None:
                if existing["sku"] != sku or int(existing["quantity"]) != quantity:
                    raise Conflict("Idempotency key already used with different parameters")
                return _reservation_record(existing)

            item = connection.execute("SELECT available FROM items WHERE sku = ?", (sku,)).fetchone()
            if item is None:
                raise NotFound(f"Item not found: {sku}")
            available = int(item["available"])
            if available < quantity:
                raise Conflict(f"Insufficient stock for item: {sku}")

            connection.execute("UPDATE items SET available = ? WHERE sku = ?", (str(available - quantity), sku))
            cursor = connection.execute(
                "INSERT INTO reservations (idempotency_key, sku, quantity, status) VALUES (?, ?, ?, 'active')",
                (key, sku, str(quantity)),
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
            # Binding decimal text also allows a valid, oversized ID to be
            # reported as missing rather than overflowing SQLite's integer.
            row = connection.execute(
                "SELECT * FROM reservations WHERE reservation_id = ?", (str(reservation_id),)
            ).fetchone()
            if row is None:
                raise NotFound(f"Reservation not found: {reservation_id}")
            record = _reservation_record(row)
            if record["status"] == "active":
                item = connection.execute("SELECT available FROM items WHERE sku = ?", (record["sku"],)).fetchone()
                available = int(item["available"]) + record["quantity"]
                connection.execute("UPDATE items SET available = ? WHERE sku = ?", (str(available), record["sku"]))
                connection.execute(
                    "UPDATE reservations SET status = 'released' WHERE reservation_id = ?", (row["reservation_id"],)
                )
                record["status"] = "released"
            return record

    def report(self):
        with self._transaction() as connection:
            items = [_item_record(row) for row in connection.execute("SELECT sku, available FROM items ORDER BY sku")]
            quantities = [int(row["quantity"]) for row in connection.execute(
                "SELECT quantity FROM reservations WHERE status = 'active'"
            )]
            return {"items": items, "active_reservations": len(quantities), "reserved_units": sum(quantities)}
