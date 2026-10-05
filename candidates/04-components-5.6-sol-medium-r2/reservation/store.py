"""SQLite persistence for the inventory reservation service."""

import os
import sqlite3


class Conflict(Exception):
    """The requested operation conflicts with the current state."""


class NotFound(Exception):
    """The requested item or reservation does not exist."""


def _nonblank(value, name):
    if not isinstance(value, str):
        raise ValueError(f"{name} must be a nonblank string")
    value = value.strip()
    if not value:
        raise ValueError(f"{name} must be a nonblank string")
    return value


def _positive_int(value, name):
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise ValueError(f"{name} must be a positive integer")
    return value


class Store:
    """A connection-per-operation SQLite inventory store."""

    def __init__(self, db_path):
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
                    sku TEXT NOT NULL REFERENCES items(sku),
                    quantity INTEGER NOT NULL CHECK (quantity > 0),
                    status TEXT NOT NULL CHECK (status IN ('active', 'released'))
                );
                """
            )
            connection.commit()
        finally:
            connection.close()

    def _connect(self):
        connection = sqlite3.connect(self.db_path, timeout=30.0)
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA busy_timeout = 30000")
        return connection

    @staticmethod
    def _reservation(row):
        return {
            "reservation_id": row[0],
            "idempotency_key": row[1],
            "sku": row[2],
            "quantity": row[3],
            "status": row[4],
        }

    def add_item(self, sku, quantity):
        sku = _nonblank(sku, "sku")
        quantity = _positive_int(quantity, "quantity")
        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute(
                """
                INSERT INTO items (sku, available) VALUES (?, ?)
                ON CONFLICT(sku) DO UPDATE
                SET available = available + excluded.available
                """,
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
        sku = _nonblank(sku, "sku")
        connection = self._connect()
        try:
            row = connection.execute(
                "SELECT available FROM items WHERE sku = ?", (sku,)
            ).fetchone()
            if row is None:
                raise NotFound(f"item not found: {sku}")
            return {"sku": sku, "available": row[0]}
        finally:
            connection.close()

    def reserve(self, key, sku, quantity):
        key = _nonblank(key, "idempotency key")
        sku = _nonblank(sku, "sku")
        quantity = _positive_int(quantity, "quantity")
        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                """
                SELECT reservation_id, idempotency_key, sku, quantity, status
                FROM reservations WHERE idempotency_key = ?
                """,
                (key,),
            ).fetchone()
            if row is not None:
                if row[2] != sku or row[3] != quantity:
                    raise Conflict("idempotency key was used with different parameters")
                connection.commit()
                return self._reservation(row)

            item = connection.execute(
                "SELECT available FROM items WHERE sku = ?", (sku,)
            ).fetchone()
            if item is None:
                raise NotFound(f"item not found: {sku}")
            if item[0] < quantity:
                raise Conflict("insufficient stock")

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
            row = (cursor.lastrowid, key, sku, quantity, "active")
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
                """
                SELECT reservation_id, idempotency_key, sku, quantity, status
                FROM reservations WHERE reservation_id = ?
                """,
                (reservation_id,),
            ).fetchone()
            if row is None:
                raise NotFound(f"reservation not found: {reservation_id}")
            if row[4] == "active":
                connection.execute(
                    "UPDATE items SET available = available + ? WHERE sku = ?",
                    (row[3], row[2]),
                )
                connection.execute(
                    "UPDATE reservations SET status = 'released' WHERE reservation_id = ?",
                    (reservation_id,),
                )
                row = row[:4] + ("released",)
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
            items = [
                {"sku": row[0], "available": row[1]}
                for row in connection.execute(
                    "SELECT sku, available FROM items ORDER BY sku"
                )
            ]
            totals = connection.execute(
                """
                SELECT COUNT(*), COALESCE(SUM(quantity), 0)
                FROM reservations WHERE status = 'active'
                """
            ).fetchone()
            connection.commit()
            return {
                "items": items,
                "active_reservations": totals[0],
                "reserved_units": totals[1],
            }
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()
