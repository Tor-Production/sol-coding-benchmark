"""SQLite-backed inventory and reservation storage."""

from __future__ import annotations

import os
import sqlite3
from contextlib import contextmanager
from typing import Iterator


class Conflict(Exception):
    """Raised when an operation conflicts with existing inventory state."""


class NotFound(Exception):
    """Raised when an item or reservation does not exist."""


_MAX_SQLITE_INTEGER = 2**63 - 1


def _nonblank_string(value: object, name: str) -> str:
    if not isinstance(value, str):
        raise ValueError(f"{name} must be a nonblank string")
    value = value.strip()
    if not value:
        raise ValueError(f"{name} must be a nonblank string")
    return value


def _positive_integer(value: object, name: str) -> int:
    if (
        isinstance(value, bool)
        or not isinstance(value, int)
        or value <= 0
        or value > _MAX_SQLITE_INTEGER
    ):
        raise ValueError(f"{name} must be a positive integer")
    return value


class Store:
    """Inventory store that opens and closes a connection for every operation."""

    def __init__(self, db_path: os.PathLike[str] | str):
        try:
            self.db_path = os.fspath(db_path)
        except TypeError as exc:
            raise ValueError("db_path must be a filesystem path") from exc

        if not isinstance(self.db_path, (str, bytes)):
            raise ValueError("db_path must be a filesystem path")

        with self._connection() as connection:
            # WAL permits readers to continue while a write transaction is in
            # progress. BEGIN IMMEDIATE below still serializes all mutations.
            connection.execute("PRAGMA journal_mode = WAL")
            connection.execute("BEGIN IMMEDIATE")
            try:
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
                connection.rollback()
                raise

    @contextmanager
    def _connection(self) -> Iterator[sqlite3.Connection]:
        connection = sqlite3.connect(self.db_path, timeout=30.0)
        try:
            connection.row_factory = sqlite3.Row
            connection.execute("PRAGMA foreign_keys = ON")
            connection.execute("PRAGMA busy_timeout = 30000")
            yield connection
        finally:
            connection.close()

    @staticmethod
    def _reservation_record(row: sqlite3.Row) -> dict[str, object]:
        return {
            "reservation_id": row["reservation_id"],
            "idempotency_key": row["idempotency_key"],
            "sku": row["sku"],
            "quantity": row["quantity"],
            "status": row["status"],
        }

    def add_item(self, sku: object, quantity: object) -> dict[str, object]:
        sku = _nonblank_string(sku, "sku")
        quantity = _positive_integer(quantity, "quantity")

        with self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                current = connection.execute(
                    "SELECT available FROM items WHERE sku = ?", (sku,)
                ).fetchone()
                available = quantity if current is None else current["available"] + quantity
                if available > _MAX_SQLITE_INTEGER:
                    raise ValueError("resulting available quantity is too large")

                if current is None:
                    connection.execute(
                        "INSERT INTO items (sku, available) VALUES (?, ?)",
                        (sku, available),
                    )
                else:
                    connection.execute(
                        "UPDATE items SET available = ? WHERE sku = ?",
                        (available, sku),
                    )
                connection.commit()
            except BaseException:
                connection.rollback()
                raise

        return {"sku": sku, "available": available}

    def get_item(self, sku: object) -> dict[str, object]:
        sku = _nonblank_string(sku, "sku")
        with self._connection() as connection:
            row = connection.execute(
                "SELECT sku, available FROM items WHERE sku = ?", (sku,)
            ).fetchone()

        if row is None:
            raise NotFound(f"item not found: {sku}")
        return {"sku": row["sku"], "available": row["available"]}

    def reserve(
        self, key: object, sku: object, quantity: object
    ) -> dict[str, object]:
        key = _nonblank_string(key, "idempotency key")
        sku = _nonblank_string(sku, "sku")
        quantity = _positive_integer(quantity, "quantity")

        with self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
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
                            "idempotency key is already used with different parameters"
                        )
                    result = self._reservation_record(existing)
                    connection.commit()
                    return result

                item = connection.execute(
                    "SELECT available FROM items WHERE sku = ?", (sku,)
                ).fetchone()
                if item is None:
                    raise NotFound(f"item not found: {sku}")
                if item["available"] < quantity:
                    raise Conflict(f"insufficient stock for item: {sku}")

                cursor = connection.execute(
                    """
                    UPDATE items
                    SET available = available - ?
                    WHERE sku = ? AND available >= ?
                    """,
                    (quantity, sku, quantity),
                )
                if cursor.rowcount != 1:
                    # The guarded update is an additional invariant check; the
                    # immediate transaction means this should only represent
                    # insufficient stock, never an oversell.
                    raise Conflict(f"insufficient stock for item: {sku}")

                cursor = connection.execute(
                    """
                    INSERT INTO reservations
                        (idempotency_key, sku, quantity, status)
                    VALUES (?, ?, ?, 'active')
                    """,
                    (key, sku, quantity),
                )
                reservation_id = cursor.lastrowid
                connection.commit()
            except BaseException:
                connection.rollback()
                raise

        return {
            "reservation_id": reservation_id,
            "idempotency_key": key,
            "sku": sku,
            "quantity": quantity,
            "status": "active",
        }

    def release(self, reservation_id: object) -> dict[str, object]:
        reservation_id = _positive_integer(reservation_id, "reservation id")

        with self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
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
                    cursor = connection.execute(
                        """
                        UPDATE reservations
                        SET status = 'released'
                        WHERE reservation_id = ? AND status = 'active'
                        """,
                        (reservation_id,),
                    )
                    if cursor.rowcount == 1:
                        connection.execute(
                            "UPDATE items SET available = available + ? WHERE sku = ?",
                            (row["quantity"], row["sku"]),
                        )

                connection.commit()
            except BaseException:
                connection.rollback()
                raise

        return {
            "reservation_id": row["reservation_id"],
            "idempotency_key": row["idempotency_key"],
            "sku": row["sku"],
            "quantity": row["quantity"],
            "status": "released",
        }

    def report(self) -> dict[str, object]:
        with self._connection() as connection:
            # An explicit read transaction makes both queries describe the same
            # snapshot even if another Store writes between them.
            connection.execute("BEGIN")
            try:
                item_rows = connection.execute(
                    "SELECT sku, available FROM items ORDER BY sku"
                ).fetchall()
                active_rows = connection.execute(
                    "SELECT quantity FROM reservations WHERE status = 'active'"
                ).fetchall()
                connection.commit()
            except BaseException:
                connection.rollback()
                raise

        return {
            "items": [
                {"sku": row["sku"], "available": row["available"]}
                for row in item_rows
            ],
            "active_reservations": len(active_rows),
            "reserved_units": sum(row["quantity"] for row in active_rows),
        }
