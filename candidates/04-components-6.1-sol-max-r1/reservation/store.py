"""Transactional, file-backed inventory storage."""

import os
import sqlite3
from contextlib import contextmanager


class Conflict(Exception):
    """Stock is insufficient or an idempotency key has different parameters."""


class NotFound(Exception):
    """The requested item or reservation does not exist."""


def _nonblank(value, name):
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} must be a nonblank string")
    return value.strip()


def _positive_integer(value, name):
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise ValueError(f"{name} must be a positive integer")
    return int(value)


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
    """Open one connection per call; serialize writes across all instances.

    Counts are stored as decimal text to preserve Python integer precision.
    Arithmetic happens while holding the SQLite write transaction.
    """

    def __init__(self, db_path):
        path = os.fspath(db_path)
        if path in ("", b"", ":memory:", b":memory:"):
            raise ValueError("db_path must name a persistent database file")
        self.db_path = os.path.abspath(path)
        with self._transaction(write=True) as connection:
            connection.execute(
                """CREATE TABLE IF NOT EXISTS items (
                    sku TEXT PRIMARY KEY NOT NULL,
                    available TEXT NOT NULL
                        CHECK (available <> '' AND
                               available NOT GLOB '*[^0-9]*')
                )"""
            )
            connection.execute(
                """CREATE TABLE IF NOT EXISTS reservations (
                    reservation_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    idempotency_key TEXT NOT NULL UNIQUE,
                    sku TEXT NOT NULL REFERENCES items(sku),
                    quantity TEXT NOT NULL
                        CHECK (quantity <> '' AND quantity <> '0' AND
                               quantity NOT GLOB '*[^0-9]*'),
                    status TEXT NOT NULL CHECK (status IN ('active', 'released'))
                )"""
            )

    @contextmanager
    def _transaction(self, *, write=False):
        connection = sqlite3.connect(
            self.db_path, timeout=30, isolation_level=None
        )
        try:
            connection.row_factory = sqlite3.Row
            connection.execute("PRAGMA foreign_keys = ON")
            connection.execute("BEGIN IMMEDIATE" if write else "BEGIN")
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
            available = (int(row["available"]) if row else 0) + quantity
            if row is None:
                connection.execute(
                    "INSERT INTO items (sku, available) VALUES (?, ?)",
                    (sku, str(available)),
                )
            else:
                connection.execute(
                    "UPDATE items SET available = ? WHERE sku = ?",
                    (str(available), sku),
                )
            return {"sku": sku, "available": available}

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
            previous = connection.execute(
                "SELECT * FROM reservations WHERE idempotency_key = ?", (key,)
            ).fetchone()
            if previous is not None:
                if previous["sku"] != sku or int(previous["quantity"]) != quantity:
                    raise Conflict("Idempotency key already has different parameters")
                return _reservation_record(previous)

            item = connection.execute(
                "SELECT available FROM items WHERE sku = ?", (sku,)
            ).fetchone()
            if item is None:
                raise NotFound(f"Item not found: {sku}")
            available = int(item["available"])
            if available < quantity:
                raise Conflict(f"Insufficient stock for SKU: {sku}")
            connection.execute(
                "UPDATE items SET available = ? WHERE sku = ?",
                (str(available - quantity), sku),
            )
            cursor = connection.execute(
                """INSERT INTO reservations (idempotency_key, sku, quantity, status)
                   VALUES (?, ?, ?, 'active')""",
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
        # SQLite-generated row IDs are signed 64-bit integers. Larger positive
        # Python integers are valid IDs to look up, but cannot exist in this DB.
        if reservation_id > (1 << 63) - 1:
            raise NotFound(f"Reservation not found: {reservation_id}")
        with self._transaction(write=True) as connection:
            row = connection.execute(
                "SELECT * FROM reservations WHERE reservation_id = ?",
                (reservation_id,),
            ).fetchone()
            if row is None:
                raise NotFound(f"Reservation not found: {reservation_id}")
            record = _reservation_record(row)
            if record["status"] == "active":
                item = connection.execute(
                    "SELECT available FROM items WHERE sku = ?", (record["sku"],)
                ).fetchone()
                connection.execute(
                    "UPDATE items SET available = ? WHERE sku = ?",
                    (str(int(item["available"]) + record["quantity"]), record["sku"]),
                )
                connection.execute(
                    "UPDATE reservations SET status = 'released' WHERE reservation_id = ?",
                    (reservation_id,),
                )
                record["status"] = "released"
            return record

    def report(self):
        # Both reads use the same snapshot, even if another connection writes.
        with self._transaction() as connection:
            items = connection.execute(
                "SELECT sku, available FROM items ORDER BY sku"
            ).fetchall()
            active = connection.execute(
                "SELECT quantity FROM reservations WHERE status = 'active'"
            ).fetchall()
            return {
                "items": [_item_record(row) for row in items],
                "active_reservations": len(active),
                "reserved_units": sum(int(row["quantity"]) for row in active),
            }
