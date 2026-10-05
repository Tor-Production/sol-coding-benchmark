"""Transactional inventory and reservations stored in SQLite."""

import os
import sqlite3
from contextlib import contextmanager


class Conflict(Exception):
    """Stock is insufficient or an idempotency key has different parameters."""


class NotFound(Exception):
    """The requested item or reservation does not exist."""


def _nonblank_string(value, name):
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
    """A connection-free handle; every operation opens and closes a connection."""

    def __init__(self, db_path):
        self.db_path = os.fspath(db_path)
        with self._transaction(write=True) as connection:
            # Decimal text preserves Python integers beyond SQLite's 64-bit range.
            # Arithmetic is performed while holding the database's write lock.
            connection.execute(
                """CREATE TABLE IF NOT EXISTS items (
                    sku TEXT PRIMARY KEY NOT NULL,
                    available TEXT NOT NULL
                )"""
            )
            connection.execute(
                """CREATE TABLE IF NOT EXISTS reservations (
                    reservation_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    idempotency_key TEXT UNIQUE NOT NULL,
                    sku TEXT NOT NULL REFERENCES items(sku),
                    quantity TEXT NOT NULL,
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
        sku = _nonblank_string(sku, "sku")
        quantity = _positive_integer(quantity, "quantity")
        with self._transaction(write=True) as connection:
            row = connection.execute(
                "SELECT sku, available FROM items WHERE sku = ?", (sku,)
            ).fetchone()
            available = quantity if row is None else int(row["available"]) + quantity
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
        sku = _nonblank_string(sku, "sku")
        with self._transaction() as connection:
            row = connection.execute(
                "SELECT sku, available FROM items WHERE sku = ?", (sku,)
            ).fetchone()
            if row is None:
                raise NotFound(f"Item not found: {sku}")
            return _item_record(row)

    def reserve(self, key, sku, quantity):
        key = _nonblank_string(key, "idempotency_key")
        sku = _nonblank_string(sku, "sku")
        quantity = _positive_integer(quantity, "quantity")
        with self._transaction(write=True) as connection:
            existing = connection.execute(
                "SELECT * FROM reservations WHERE idempotency_key = ?", (key,)
            ).fetchone()
            if existing is not None:
                if existing["sku"] != sku or int(existing["quantity"]) != quantity:
                    raise Conflict("Idempotency key already has different parameters")
                return _reservation_record(existing)

            item = connection.execute(
                "SELECT available FROM items WHERE sku = ?", (sku,)
            ).fetchone()
            if item is None:
                raise NotFound(f"Item not found: {sku}")
            available = int(item["available"])
            if available < quantity:
                raise Conflict(f"Insufficient stock for: {sku}")

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
        with self._transaction(write=True) as connection:
            row = connection.execute(
                "SELECT * FROM reservations WHERE reservation_id = ?",
                (str(reservation_id),),
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
        # One read transaction keeps items and reservation totals in one snapshot.
        with self._transaction() as connection:
            items = [
                _item_record(row)
                for row in connection.execute("SELECT sku, available FROM items ORDER BY sku")
            ]
            active = connection.execute(
                "SELECT quantity FROM reservations WHERE status = 'active'"
            ).fetchall()
            return {
                "items": items,
                "active_reservations": len(active),
                "reserved_units": sum(int(row["quantity"]) for row in active),
            }
