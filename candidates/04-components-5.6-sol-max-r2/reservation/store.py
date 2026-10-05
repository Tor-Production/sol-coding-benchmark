"""SQLite-backed inventory and reservation storage."""

from __future__ import annotations

import os
import sqlite3
from typing import Any


class Conflict(Exception):
    """The requested operation conflicts with the current stored state."""


class NotFound(Exception):
    """The requested item or reservation does not exist."""


class Store:
    """Persistent inventory storage.

    A ``Store`` does not retain an open SQLite connection. Each public
    operation opens and closes its own connection, which makes an instance
    safe to share between request threads and lets multiple instances use the
    same database file.
    """

    _BUSY_TIMEOUT_MS = 30_000

    def __init__(self, db_path: os.PathLike[str] | str):
        self._db_path = os.fspath(db_path)
        self._initialize_schema()

    @staticmethod
    def _normalized_text(value: Any, name: str) -> str:
        if not isinstance(value, str):
            raise ValueError(f"{name} must be a nonblank string")
        value = value.strip()
        if not value:
            raise ValueError(f"{name} must be a nonblank string")
        return value

    @staticmethod
    def _positive_integer(value: Any, name: str) -> int:
        if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
            raise ValueError(f"{name} must be a positive integer")
        return value

    @staticmethod
    def _item_record(row: sqlite3.Row) -> dict[str, Any]:
        return {"sku": row["sku"], "available": row["available"]}

    @staticmethod
    def _reservation_record(row: sqlite3.Row) -> dict[str, Any]:
        return {
            "reservation_id": row["reservation_id"],
            "idempotency_key": row["idempotency_key"],
            "sku": row["sku"],
            "quantity": row["quantity"],
            "status": row["status"],
        }

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(
            self._db_path,
            timeout=self._BUSY_TIMEOUT_MS / 1000,
            isolation_level=None,
        )
        connection.row_factory = sqlite3.Row
        connection.execute(f"PRAGMA busy_timeout = {self._BUSY_TIMEOUT_MS}")
        connection.execute("PRAGMA foreign_keys = ON")
        return connection

    def _initialize_schema(self) -> None:
        connection = self._connect()
        try:
            # WAL readers do not block writers, while BEGIN IMMEDIATE below
            # still serializes each stock-changing operation.
            connection.execute("PRAGMA journal_mode = WAL")
            connection.execute("BEGIN IMMEDIATE")
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS items (
                    sku TEXT PRIMARY KEY,
                    available INTEGER NOT NULL
                        CHECK (typeof(available) = 'integer' AND available >= 0)
                )
                """
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS reservations (
                    reservation_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    idempotency_key TEXT NOT NULL UNIQUE,
                    sku TEXT NOT NULL,
                    quantity INTEGER NOT NULL
                        CHECK (typeof(quantity) = 'integer' AND quantity > 0),
                    status TEXT NOT NULL
                        CHECK (status IN ('active', 'released')),
                    FOREIGN KEY (sku) REFERENCES items(sku)
                )
                """
            )
            connection.commit()
        except BaseException:
            if connection.in_transaction:
                connection.rollback()
            raise
        finally:
            connection.close()

    def add_item(self, sku: str, quantity: int) -> dict[str, Any]:
        sku = self._normalized_text(sku, "sku")
        quantity = self._positive_integer(quantity, "quantity")

        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute(
                """
                INSERT INTO items (sku, available) VALUES (?, ?)
                ON CONFLICT(sku) DO UPDATE
                    SET available = items.available + excluded.available
                """,
                (sku, quantity),
            )
            row = connection.execute(
                "SELECT sku, available FROM items WHERE sku = ?", (sku,)
            ).fetchone()
            connection.commit()
            return self._item_record(row)
        except BaseException:
            if connection.in_transaction:
                connection.rollback()
            raise
        finally:
            connection.close()

    def get_item(self, sku: str) -> dict[str, Any]:
        sku = self._normalized_text(sku, "sku")

        connection = self._connect()
        try:
            row = connection.execute(
                "SELECT sku, available FROM items WHERE sku = ?", (sku,)
            ).fetchone()
            if row is None:
                raise NotFound(f"item not found: {sku}")
            return self._item_record(row)
        finally:
            connection.close()

    def reserve(self, key: str, sku: str, quantity: int) -> dict[str, Any]:
        key = self._normalized_text(key, "idempotency key")
        sku = self._normalized_text(sku, "sku")
        quantity = self._positive_integer(quantity, "quantity")

        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")

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
                record = self._reservation_record(existing)
                connection.commit()
                return record

            item = connection.execute(
                "SELECT available FROM items WHERE sku = ?", (sku,)
            ).fetchone()
            if item is None:
                raise NotFound(f"item not found: {sku}")
            if item["available"] < quantity:
                raise Conflict(f"insufficient stock for item: {sku}")

            updated = connection.execute(
                """
                UPDATE items
                SET available = available - ?
                WHERE sku = ? AND available >= ?
                """,
                (quantity, sku, quantity),
            )
            if updated.rowcount != 1:
                # The predicate is also a defensive guard if this method is
                # ever changed to use a less restrictive transaction mode.
                raise Conflict(f"insufficient stock for item: {sku}")

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
            connection.commit()
            return self._reservation_record(row)
        except BaseException:
            if connection.in_transaction:
                connection.rollback()
            raise
        finally:
            connection.close()

    def release(self, reservation_id: int) -> dict[str, Any]:
        reservation_id = self._positive_integer(
            reservation_id, "reservation id"
        )

        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
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

            record = self._reservation_record(row)
            connection.commit()
            return record
        except BaseException:
            if connection.in_transaction:
                connection.rollback()
            raise
        finally:
            connection.close()

    def report(self) -> dict[str, Any]:
        connection = self._connect()
        try:
            # An explicit read transaction keeps both parts of the report on
            # the same snapshot while writers are active.
            connection.execute("BEGIN")
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
            result = {
                "items": [self._item_record(row) for row in item_rows],
                "active_reservations": totals["active_reservations"],
                "reserved_units": totals["reserved_units"],
            }
            connection.commit()
            return result
        except BaseException:
            if connection.in_transaction:
                connection.rollback()
            raise
        finally:
            connection.close()
