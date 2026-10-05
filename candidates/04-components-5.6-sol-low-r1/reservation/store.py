"""Persistent SQLite store for inventory reservations."""

import os
import sqlite3


class Conflict(Exception):
    """The requested operation conflicts with existing state."""


class NotFound(Exception):
    """The requested entity does not exist."""


def _text(value, name):
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} must be a nonblank string")
    return value.strip()


def _positive_int(value, name):
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise ValueError(f"{name} must be a positive integer")
    return value


class Store:
    def __init__(self, db_path):
        if not isinstance(db_path, (str, bytes, os.PathLike)):
            raise ValueError("db_path must be a path")
        self.db_path = os.fspath(db_path)
        connection = self._connect()
        try:
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
        finally:
            connection.close()

    def _connect(self):
        connection = sqlite3.connect(self.db_path, timeout=30)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA busy_timeout = 30000")
        return connection

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
        quantity = _positive_int(quantity, "quantity")
        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute(
                """INSERT INTO items (sku, available) VALUES (?, ?)
                   ON CONFLICT(sku) DO UPDATE
                   SET available = available + excluded.available""",
                (sku, quantity),
            )
            available = connection.execute(
                "SELECT available FROM items WHERE sku = ?", (sku,)
            ).fetchone()[0]
            connection.commit()
            return {"sku": sku, "available": available}
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def get_item(self, sku):
        sku = _text(sku, "sku")
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

    def reserve(self, key, sku, quantity):
        key = _text(key, "idempotency_key")
        sku = _text(sku, "sku")
        quantity = _positive_int(quantity, "quantity")
        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            existing = connection.execute(
                "SELECT * FROM reservations WHERE idempotency_key = ?", (key,)
            ).fetchone()
            if existing is not None:
                if existing["sku"] != sku or existing["quantity"] != quantity:
                    raise Conflict("idempotency key used with different parameters")
                connection.commit()
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
                "SELECT * FROM reservations WHERE reservation_id = ?",
                (cursor.lastrowid,),
            ).fetchone()
            connection.commit()
            return self._reservation(row)
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def release(self, reservation_id):
        reservation_id = _positive_int(reservation_id, "reservation_id")
        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT * FROM reservations WHERE reservation_id = ?",
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
                row = connection.execute(
                    "SELECT * FROM reservations WHERE reservation_id = ?",
                    (reservation_id,),
                ).fetchone()
            connection.commit()
            return self._reservation(row)
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def report(self):
        connection = self._connect()
        try:
            connection.execute("BEGIN")
            rows = connection.execute(
                "SELECT sku, available FROM items ORDER BY sku"
            ).fetchall()
            totals = connection.execute(
                """SELECT COUNT(*) AS count, COALESCE(SUM(quantity), 0) AS units
                   FROM reservations WHERE status = 'active'"""
            ).fetchone()
            connection.commit()
            return {
                "items": [
                    {"sku": row["sku"], "available": row["available"]}
                    for row in rows
                ],
                "active_reservations": totals["count"],
                "reserved_units": totals["units"],
            }
        finally:
            connection.close()
