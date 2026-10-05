"""Transactional inventory and reservation storage."""

import os
import sqlite3
from contextlib import contextmanager


class Conflict(Exception):
    """The requested operation conflicts with stock or an existing key."""


class NotFound(Exception):
    """The requested item or reservation does not exist."""


def _nonblank(value, name):
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
    """A file-backed store with a fresh, closed connection for every call."""

    def __init__(self, db_path):
        self.db_path = os.fspath(db_path)
        with self._transaction(write=True) as connection:
            connection.execute(
                """CREATE TABLE IF NOT EXISTS items (
                    sku TEXT PRIMARY KEY NOT NULL,
                    available TEXT NOT NULL
                        CHECK (available <> '' AND available NOT GLOB '*[^0-9]*')
                )"""
            )
            connection.execute(
                """CREATE TABLE IF NOT EXISTS reservations (
                    reservation_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    idempotency_key TEXT NOT NULL UNIQUE,
                    sku TEXT NOT NULL REFERENCES items(sku),
                    quantity TEXT NOT NULL
                        CHECK (quantity <> '' AND quantity NOT GLOB '*[^0-9]*'
                               AND substr(quantity, 1, 1) BETWEEN '1' AND '9'),
                    status TEXT NOT NULL CHECK (status IN ('active', 'released'))
                )"""
            )

    @contextmanager
    def _transaction(self, write=False):
        connection = sqlite3.connect(self.db_path, timeout=30, isolation_level=None)
        try:
            connection.row_factory = sqlite3.Row
            connection.execute("PRAGMA foreign_keys = ON")
            # Acquire the writer lock before reading stock or idempotency keys.
            # This also serializes writers using other Store instances/processes.
            connection.execute("BEGIN IMMEDIATE" if write else "BEGIN")
            try:
                yield connection
                connection.commit()
            except BaseException:
                connection.rollback()
                raise
        finally:
            connection.close()

    def add_item(self, sku, quantity):
        sku = _nonblank(sku, "sku")
        quantity = _positive_integer(quantity, "quantity")
        with self._transaction(write=True) as connection:
            row = connection.execute(
                "SELECT available FROM items WHERE sku = ?", (sku,)
            ).fetchone()
            available = quantity + (int(row["available"]) if row is not None else 0)
            # Decimal text preserves Python integers beyond SQLite's 64-bit limit.
            # Arithmetic is protected by the same writer transaction as the SQL.
            connection.execute(
                """INSERT INTO items (sku, available) VALUES (?, ?)
                   ON CONFLICT(sku) DO UPDATE
                   SET available = excluded.available""",
                (sku, str(available)),
            )
            row = connection.execute(
                "SELECT sku, available FROM items WHERE sku = ?", (sku,)
            ).fetchone()
            return _item_record(row)

    def get_item(self, sku):
        sku = _nonblank(sku, "sku")
        with self._transaction() as connection:
            row = connection.execute(
                "SELECT sku, available FROM items WHERE sku = ?", (sku,)
            ).fetchone()
            if row is None:
                raise NotFound(f"Item not found: {sku}")
            return _item_record(row)

    def reserve(self, key, sku, quantity):
        key = _nonblank(key, "idempotency_key")
        sku = _nonblank(sku, "sku")
        quantity = _positive_integer(quantity, "quantity")
        with self._transaction(write=True) as connection:
            existing = connection.execute(
                "SELECT * FROM reservations WHERE idempotency_key = ?", (key,)
            ).fetchone()
            if existing is not None:
                if existing["sku"] != sku or int(existing["quantity"]) != quantity:
                    raise Conflict("Idempotency key was used with different parameters")
                # A replay returns the original successful reservation response,
                # even if a later release has changed the stored lifecycle state.
                record = _reservation_record(existing)
                record["status"] = "active"
                return record

            item = connection.execute(
                "SELECT available FROM items WHERE sku = ?", (sku,)
            ).fetchone()
            if item is None:
                raise NotFound(f"Item not found: {sku}")
            available = int(item["available"])
            if available < quantity:
                raise Conflict(f"Insufficient stock for item: {sku}")

            connection.execute(
                "UPDATE items SET available = ? WHERE sku = ?",
                (str(available - quantity), sku),
            )
            cursor = connection.execute(
                """INSERT INTO reservations (idempotency_key, sku, quantity, status)
                   VALUES (?, ?, ?, 'active')""",
                (key, sku, str(quantity)),
            )
            row = connection.execute(
                "SELECT * FROM reservations WHERE reservation_id = ?",
                (cursor.lastrowid,),
            ).fetchone()
            return _reservation_record(row)

    def release(self, reservation_id):
        reservation_id = _positive_integer(reservation_id, "reservation_id")
        if reservation_id > (1 << 63) - 1:
            # Such an ID cannot be generated by SQLite, but is a valid argument.
            raise NotFound(f"Reservation not found: {reservation_id}")
        with self._transaction(write=True) as connection:
            row = connection.execute(
                "SELECT * FROM reservations WHERE reservation_id = ?",
                (reservation_id,),
            ).fetchone()
            if row is None:
                raise NotFound(f"Reservation not found: {reservation_id}")
            if row["status"] == "active":
                item = connection.execute(
                    "SELECT available FROM items WHERE sku = ?", (row["sku"],)
                ).fetchone()
                available = int(item["available"]) + int(row["quantity"])
                connection.execute(
                    "UPDATE items SET available = ? WHERE sku = ?",
                    (str(available), row["sku"]),
                )
                connection.execute(
                    "UPDATE reservations SET status = 'released' WHERE reservation_id = ?",
                    (reservation_id,),
                )
            record = _reservation_record(row)
            record["status"] = "released"
            return record

    def report(self):
        with self._transaction() as connection:
            items = connection.execute(
                "SELECT sku, available FROM items ORDER BY sku"
            ).fetchall()
            # Both queries share a read transaction, so the report is a snapshot.
            active = connection.execute(
                "SELECT quantity FROM reservations WHERE status = 'active'"
            ).fetchall()
            return {
                "items": [_item_record(row) for row in items],
                "active_reservations": len(active),
                "reserved_units": sum(int(row["quantity"]) for row in active),
            }
