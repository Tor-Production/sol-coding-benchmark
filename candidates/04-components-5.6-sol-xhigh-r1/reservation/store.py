"""Persistent inventory and reservation storage."""

import os
import sqlite3
from contextlib import contextmanager, closing


class Conflict(Exception):
    """The requested operation conflicts with current stored state."""


class NotFound(Exception):
    """The requested item or reservation does not exist."""


class Store:
    """A small SQLite-backed inventory store.

    Connections are deliberately short-lived.  Write operations use
    ``BEGIN IMMEDIATE`` so the stock check and its corresponding mutation are
    serialized even when several Store instances use the same database.
    """

    def __init__(self, db_path):
        try:
            self.db_path = os.fspath(db_path)
        except TypeError as exc:
            raise ValueError("db_path must be a filesystem path") from exc

        if not isinstance(self.db_path, (str, bytes)) or not self.db_path:
            raise ValueError("db_path must be a non-empty filesystem path")

        with self._transaction(immediate=True) as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS items (
                    sku TEXT PRIMARY KEY,
                    available INTEGER NOT NULL CHECK (available >= 0)
                )
                """
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS reservations (
                    reservation_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    idempotency_key TEXT NOT NULL UNIQUE,
                    sku TEXT NOT NULL,
                    quantity INTEGER NOT NULL CHECK (quantity > 0),
                    status TEXT NOT NULL
                        CHECK (status IN ('active', 'released')),
                    FOREIGN KEY (sku) REFERENCES items (sku)
                )
                """
            )

    def _connect(self):
        connection = sqlite3.connect(
            self.db_path,
            timeout=30.0,
            isolation_level=None,
        )
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA busy_timeout = 30000")
        return connection

    @contextmanager
    def _transaction(self, *, immediate=False):
        with closing(self._connect()) as connection:
            try:
                connection.execute("BEGIN IMMEDIATE" if immediate else "BEGIN")
                yield connection
                connection.commit()
            except BaseException:
                if connection.in_transaction:
                    connection.rollback()
                raise

    @staticmethod
    def _text(value, name):
        if not isinstance(value, str):
            raise ValueError(f"{name} must be a nonblank string")
        value = value.strip()
        if not value:
            raise ValueError(f"{name} must be a nonblank string")
        return value

    @staticmethod
    def _positive_integer(value, name):
        if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
            raise ValueError(f"{name} must be a positive integer")
        return value

    @staticmethod
    def _item_record(row):
        return {"sku": row["sku"], "available": row["available"]}

    @staticmethod
    def _reservation_record(row):
        return {
            "reservation_id": row["reservation_id"],
            "idempotency_key": row["idempotency_key"],
            "sku": row["sku"],
            "quantity": row["quantity"],
            "status": row["status"],
        }

    def add_item(self, sku, quantity):
        sku = self._text(sku, "sku")
        quantity = self._positive_integer(quantity, "quantity")

        with self._transaction(immediate=True) as connection:
            connection.execute(
                """
                INSERT INTO items (sku, available) VALUES (?, ?)
                ON CONFLICT(sku) DO UPDATE
                    SET available = available + excluded.available
                """,
                (sku, quantity),
            )
            row = connection.execute(
                "SELECT sku, available FROM items WHERE sku = ?", (sku,)
            ).fetchone()
            return self._item_record(row)

    def get_item(self, sku):
        sku = self._text(sku, "sku")

        with closing(self._connect()) as connection:
            row = connection.execute(
                "SELECT sku, available FROM items WHERE sku = ?", (sku,)
            ).fetchone()
        if row is None:
            raise NotFound(f"item not found: {sku}")
        return self._item_record(row)

    def reserve(self, key, sku, quantity):
        key = self._text(key, "idempotency key")
        sku = self._text(sku, "sku")
        quantity = self._positive_integer(quantity, "quantity")

        with self._transaction(immediate=True) as connection:
            existing = connection.execute(
                """
                SELECT reservation_id, idempotency_key, sku, quantity, status
                FROM reservations
                WHERE idempotency_key = ?
                """,
                (key,),
            ).fetchone()
            if existing is not None:
                if existing["sku"] != sku or existing["quantity"] != quantity:
                    raise Conflict(
                        "idempotency key was already used with different parameters"
                    )
                return self._reservation_record(existing)

            item = connection.execute(
                "SELECT available FROM items WHERE sku = ?", (sku,)
            ).fetchone()
            if item is None:
                raise NotFound(f"item not found: {sku}")
            if item["available"] < quantity:
                raise Conflict(f"insufficient stock for item: {sku}")

            connection.execute(
                "UPDATE items SET available = available - ? WHERE sku = ?",
                (quantity, sku),
            )
            cursor = connection.execute(
                """
                INSERT INTO reservations
                    (idempotency_key, sku, quantity, status)
                VALUES (?, ?, ?, 'active')
                """,
                (key, sku, quantity),
            )
            row = connection.execute(
                """
                SELECT reservation_id, idempotency_key, sku, quantity, status
                FROM reservations
                WHERE reservation_id = ?
                """,
                (cursor.lastrowid,),
            ).fetchone()
            return self._reservation_record(row)

    def release(self, reservation_id):
        reservation_id = self._positive_integer(
            reservation_id, "reservation_id"
        )

        with self._transaction(immediate=True) as connection:
            row = connection.execute(
                """
                SELECT reservation_id, idempotency_key, sku, quantity, status
                FROM reservations
                WHERE reservation_id = ?
                """,
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
                    """
                    UPDATE reservations
                    SET status = 'released'
                    WHERE reservation_id = ?
                    """,
                    (reservation_id,),
                )
                row = connection.execute(
                    """
                    SELECT reservation_id, idempotency_key, sku, quantity, status
                    FROM reservations
                    WHERE reservation_id = ?
                    """,
                    (reservation_id,),
                ).fetchone()

            return self._reservation_record(row)

    def report(self):
        with self._transaction() as connection:
            item_rows = connection.execute(
                "SELECT sku, available FROM items ORDER BY sku"
            ).fetchall()
            totals = connection.execute(
                """
                SELECT COUNT(*) AS active_reservations,
                       COALESCE(SUM(quantity), 0) AS reserved_units
                FROM reservations
                WHERE status = 'active'
                """
            ).fetchone()

        return {
            "items": [self._item_record(row) for row in item_rows],
            "active_reservations": totals["active_reservations"],
            "reserved_units": totals["reserved_units"],
        }
