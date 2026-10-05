"""SQLite persistence for the inventory reservation service."""

from __future__ import annotations

import os
import sqlite3
from typing import Any


class Conflict(Exception):
    """The requested operation conflicts with existing state."""


class NotFound(Exception):
    """The requested item or reservation does not exist."""


_MAX_SQLITE_INTEGER = (1 << 63) - 1


def _nonblank_string(value: Any, name: str) -> str:
    if not isinstance(value, str):
        raise ValueError(f"{name} must be a string")
    value = value.strip()
    if not value:
        raise ValueError(f"{name} must not be blank")
    return value


def _positive_integer(value: Any, name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise ValueError(f"{name} must be a positive integer")
    # SQLite INTEGER values are signed 64-bit values. Rejecting larger input
    # gives every interface a validation error instead of an OverflowError.
    if value > _MAX_SQLITE_INTEGER:
        raise ValueError(f"{name} is too large")
    return value


def _reservation_record(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "reservation_id": row["reservation_id"],
        "idempotency_key": row["idempotency_key"],
        "sku": row["sku"],
        "quantity": row["quantity"],
        "status": row["status"],
    }


class Store:
    """A connection-per-operation SQLite inventory store.

    Write operations use ``BEGIN IMMEDIATE`` so the stock check and mutation
    are one serialized transaction across separate Store instances.
    """

    def __init__(self, db_path: str | os.PathLike[str]):
        try:
            self.db_path = os.fspath(db_path)
        except TypeError as exc:
            raise ValueError("db_path must be a path") from exc
        if not isinstance(self.db_path, (str, bytes)) or not self.db_path:
            raise ValueError("db_path must be a non-empty path")

        connection = self._connect()
        try:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS items (
                    sku TEXT PRIMARY KEY,
                    available INTEGER NOT NULL CHECK (
                        typeof(available) = 'integer' AND available >= 0
                    )
                );

                CREATE TABLE IF NOT EXISTS reservations (
                    reservation_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    idempotency_key TEXT NOT NULL UNIQUE,
                    sku TEXT NOT NULL REFERENCES items(sku),
                    quantity INTEGER NOT NULL CHECK (
                        typeof(quantity) = 'integer' AND quantity > 0
                    ),
                    status TEXT NOT NULL CHECK (status IN ('active', 'released'))
                );

                CREATE INDEX IF NOT EXISTS reservations_status_idx
                    ON reservations(status);
                """
            )
        finally:
            connection.close()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.db_path, timeout=30.0)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA busy_timeout = 30000")
        return connection

    def add_item(self, sku: str, quantity: int) -> dict[str, Any]:
        sku = _nonblank_string(sku, "sku")
        quantity = _positive_integer(quantity, "quantity")

        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT available FROM items WHERE sku = ?", (sku,)
            ).fetchone()
            if row is None:
                available = quantity
                connection.execute(
                    "INSERT INTO items (sku, available) VALUES (?, ?)",
                    (sku, available),
                )
            else:
                available = row["available"] + quantity
                if available > _MAX_SQLITE_INTEGER:
                    raise ValueError("resulting available quantity is too large")
                connection.execute(
                    "UPDATE items SET available = ? WHERE sku = ?",
                    (available, sku),
                )
            connection.commit()
            return {"sku": sku, "available": available}
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def get_item(self, sku: str) -> dict[str, Any]:
        sku = _nonblank_string(sku, "sku")
        connection = self._connect()
        try:
            row = connection.execute(
                "SELECT sku, available FROM items WHERE sku = ?", (sku,)
            ).fetchone()
            if row is None:
                raise NotFound(f"item not found: {sku}")
            return {"sku": row["sku"], "available": row["available"]}
        finally:
            connection.close()

    def reserve(self, key: str, sku: str, quantity: int) -> dict[str, Any]:
        key = _nonblank_string(key, "idempotency key")
        sku = _nonblank_string(sku, "sku")
        quantity = _positive_integer(quantity, "quantity")

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
                        "idempotency key already used with different parameters"
                    )
                connection.commit()
                return _reservation_record(existing)

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
            reservation_id = cursor.lastrowid
            connection.commit()
            return {
                "reservation_id": reservation_id,
                "idempotency_key": key,
                "sku": sku,
                "quantity": quantity,
                "status": "active",
            }
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def release(self, reservation_id: int) -> dict[str, Any]:
        reservation_id = _positive_integer(reservation_id, "reservation_id")

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
                current = connection.execute(
                    "SELECT available FROM items WHERE sku = ?", (row["sku"],)
                ).fetchone()
                # This is impossible with the private schema, but retaining the
                # check keeps a tampered database from producing a partial write.
                if current is None:
                    raise NotFound(f"item not found: {row['sku']}")
                restored = current["available"] + row["quantity"]
                if restored > _MAX_SQLITE_INTEGER:
                    raise ValueError("resulting available quantity is too large")
                connection.execute(
                    "UPDATE items SET available = ? WHERE sku = ?",
                    (restored, row["sku"]),
                )
                connection.execute(
                    """
                    UPDATE reservations SET status = 'released'
                    WHERE reservation_id = ?
                    """,
                    (reservation_id,),
                )

            result = {
                "reservation_id": row["reservation_id"],
                "idempotency_key": row["idempotency_key"],
                "sku": row["sku"],
                "quantity": row["quantity"],
                "status": "released",
            }
            connection.commit()
            return result
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def report(self) -> dict[str, Any]:
        connection = self._connect()
        try:
            connection.execute("BEGIN")
            item_rows = connection.execute(
                "SELECT sku, available FROM items ORDER BY sku"
            ).fetchall()
            active_rows = connection.execute(
                """
                SELECT quantity
                FROM reservations
                WHERE status = 'active'
                """
            ).fetchall()
            result = {
                "items": [
                    {"sku": row["sku"], "available": row["available"]}
                    for row in item_rows
                ],
                "active_reservations": len(active_rows),
                # Sum in Python so totals spanning many SKUs are not limited
                # by SQLite's signed 64-bit SUM accumulator.
                "reserved_units": sum(row["quantity"] for row in active_rows),
            }
            connection.commit()
            return result
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()
