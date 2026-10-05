"""Persistent SQLite storage for the inventory reservation service."""

import sqlite3
from contextlib import contextmanager
from os import fspath


class Conflict(Exception):
    """The requested operation conflicts with the current stored state."""


class NotFound(Exception):
    """The requested record does not exist."""


def _text(value, name):
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} must be a nonblank string")
    return value.strip()


def _positive_integer(value, name):
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise ValueError(f"{name} must be a positive integer")
    return value


class Store:
    """A connection-per-operation, concurrency-safe inventory store."""

    def __init__(self, db_path):
        self.db_path = fspath(db_path)
        if not self.db_path:
            raise ValueError("db_path must not be empty")
        with self._connection() as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS items (
                    sku TEXT PRIMARY KEY,
                    available INTEGER NOT NULL CHECK (available >= 0)
                );
                CREATE TABLE IF NOT EXISTS reservations (
                    reservation_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    idempotency_key TEXT NOT NULL UNIQUE,
                    sku TEXT NOT NULL,
                    quantity INTEGER NOT NULL CHECK (quantity > 0),
                    status TEXT NOT NULL CHECK (status IN ('active', 'released')),
                    FOREIGN KEY (sku) REFERENCES items(sku)
                );
                """
            )

    @contextmanager
    def _connection(self):
        connection = sqlite3.connect(self.db_path, timeout=30.0)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA busy_timeout = 30000")
        try:
            yield connection
            connection.commit()
        except BaseException:
            connection.rollback()
            raise
        finally:
            connection.close()

    @staticmethod
    def _reservation(row):
        return {
            "reservation_id": row["reservation_id"],
            "idempotency_key": row["idempotency_key"],
            "sku": row["sku"],
            "quantity": row["quantity"],
            "status": row["status"],
        }

    def add_item(self, sku, quantity):
        sku = _text(sku, "sku")
        quantity = _positive_integer(quantity, "quantity")
        with self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute(
                """INSERT INTO items (sku, available) VALUES (?, ?)
                   ON CONFLICT(sku) DO UPDATE
                   SET available = available + excluded.available""",
                (sku, quantity),
            )
            row = connection.execute(
                "SELECT sku, available FROM items WHERE sku = ?", (sku,)
            ).fetchone()
            return {"sku": row["sku"], "available": row["available"]}

    def get_item(self, sku):
        sku = _text(sku, "sku")
        with self._connection() as connection:
            row = connection.execute(
                "SELECT sku, available FROM items WHERE sku = ?", (sku,)
            ).fetchone()
            if row is None:
                raise NotFound(f"item not found: {sku}")
            return {"sku": row["sku"], "available": row["available"]}

    def reserve(self, key, sku, quantity):
        key = _text(key, "idempotency_key")
        sku = _text(sku, "sku")
        quantity = _positive_integer(quantity, "quantity")
        with self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            existing = connection.execute(
                """SELECT reservation_id, idempotency_key, sku, quantity, status
                   FROM reservations WHERE idempotency_key = ?""",
                (key,),
            ).fetchone()
            if existing is not None:
                if existing["sku"] != sku or existing["quantity"] != quantity:
                    raise Conflict("idempotency key already used with different parameters")
                return self._reservation(existing)

            item = connection.execute(
                "SELECT available FROM items WHERE sku = ?", (sku,)
            ).fetchone()
            if item is None:
                raise NotFound(f"item not found: {sku}")
            if item["available"] < quantity:
                raise Conflict("insufficient stock")
            connection.execute(
                "UPDATE items SET available = available - ? WHERE sku = ?",
                (quantity, sku),
            )
            cursor = connection.execute(
                """INSERT INTO reservations
                       (idempotency_key, sku, quantity, status)
                   VALUES (?, ?, ?, 'active')""",
                (key, sku, quantity),
            )
            row = connection.execute(
                """SELECT reservation_id, idempotency_key, sku, quantity, status
                   FROM reservations WHERE reservation_id = ?""",
                (cursor.lastrowid,),
            ).fetchone()
            return self._reservation(row)

    def release(self, reservation_id):
        reservation_id = _positive_integer(reservation_id, "reservation_id")
        with self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                """SELECT reservation_id, idempotency_key, sku, quantity, status
                   FROM reservations WHERE reservation_id = ?""",
                (reservation_id,),
            ).fetchone()
            if row is None:
                raise NotFound(f"reservation not found: {reservation_id}")
            if row["status"] == "active":
                connection.execute(
                    "UPDATE items SET available = available + ? WHERE sku = ?",
                    (row["quantity"], row["sku"]),
                )
                connection.execute(
                    "UPDATE reservations SET status = 'released' WHERE reservation_id = ?",
                    (reservation_id,),
                )
                result = dict(row)
                result["status"] = "released"
                return self._reservation(result)
            return self._reservation(row)

    def report(self):
        with self._connection() as connection:
            # Keep both queries on the same snapshot if a writer commits
            # between them.
            connection.execute("BEGIN")
            item_rows = connection.execute(
                "SELECT sku, available FROM items ORDER BY sku"
            ).fetchall()
            totals = connection.execute(
                """SELECT COUNT(*) AS active_reservations,
                          COALESCE(SUM(quantity), 0) AS reserved_units
                   FROM reservations WHERE status = 'active'"""
            ).fetchone()
            return {
                "items": [
                    {"sku": row["sku"], "available": row["available"]}
                    for row in item_rows
                ],
                "active_reservations": totals["active_reservations"],
                "reserved_units": totals["reserved_units"],
            }
