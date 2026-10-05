"""SQLite persistence for the inventory reservation service."""

import os
import sqlite3


class Conflict(Exception):
    """The requested operation conflicts with the current stored state."""


class NotFound(Exception):
    """A requested item or reservation does not exist."""


_MAX_SQLITE_INTEGER = (1 << 63) - 1


def _clean_string(value, name):
    if not isinstance(value, str):
        raise ValueError(f"{name} must be a nonblank string")
    value = value.strip()
    if not value:
        raise ValueError(f"{name} must be a nonblank string")
    return value


def _positive_integer(value, name):
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise ValueError(f"{name} must be a positive integer")
    if value > _MAX_SQLITE_INTEGER:
        raise ValueError(f"{name} is too large")
    return value


class Store:
    """A connection-per-operation SQLite store.

    No connection is retained by an instance, so a ``Store`` can safely be
    shared by request-handler threads.  ``BEGIN IMMEDIATE`` serializes each
    stock-changing operation with writers from other Store instances.
    """

    def __init__(self, db_path):
        if isinstance(db_path, os.PathLike):
            db_path = os.fspath(db_path)
        if not isinstance(db_path, str) or not db_path:
            raise ValueError("db_path must be a nonempty path")
        self.db_path = db_path
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

                CREATE INDEX IF NOT EXISTS reservations_status_index
                    ON reservations(status);
                """
            )
            connection.commit()
        finally:
            connection.close()

    def _connect(self):
        connection = sqlite3.connect(
            self.db_path,
            timeout=30.0,
            isolation_level=None,
        )
        try:
            connection.execute("PRAGMA busy_timeout = 30000")
            connection.execute("PRAGMA foreign_keys = ON")
            return connection
        except Exception:
            connection.close()
            raise

    @staticmethod
    def _item_record(row):
        return {"sku": row[0], "available": row[1]}

    @staticmethod
    def _reservation_record(row):
        return {
            "reservation_id": row[0],
            "idempotency_key": row[1],
            "sku": row[2],
            "quantity": row[3],
            "status": row[4],
        }

    def add_item(self, sku, quantity):
        sku = _clean_string(sku, "sku")
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
                    "INSERT INTO items(sku, available) VALUES (?, ?)",
                    (sku, available),
                )
            else:
                if row[0] > _MAX_SQLITE_INTEGER - quantity:
                    raise ValueError("resulting quantity is too large")
                available = row[0] + quantity
                connection.execute(
                    "UPDATE items SET available = ? WHERE sku = ?",
                    (available, sku),
                )
            connection.commit()
            return {"sku": sku, "available": available}
        except Exception:
            if connection.in_transaction:
                connection.rollback()
            raise
        finally:
            connection.close()

    def get_item(self, sku):
        sku = _clean_string(sku, "sku")
        connection = self._connect()
        try:
            row = connection.execute(
                "SELECT sku, available FROM items WHERE sku = ?", (sku,)
            ).fetchone()
            if row is None:
                raise NotFound(f"item {sku!r} not found")
            return self._item_record(row)
        finally:
            connection.close()

    def reserve(self, key, sku, quantity):
        key = _clean_string(key, "idempotency key")
        sku = _clean_string(sku, "sku")
        quantity = _positive_integer(quantity, "quantity")
        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")

            existing = connection.execute(
                """
                SELECT reservation_id, idempotency_key, sku, quantity, status
                FROM reservations WHERE idempotency_key = ?
                """,
                (key,),
            ).fetchone()
            if existing is not None:
                if existing[2] != sku or existing[3] != quantity:
                    raise Conflict(
                        "idempotency key was already used with different parameters"
                    )
                connection.commit()
                return self._reservation_record(existing)

            item = connection.execute(
                "SELECT available FROM items WHERE sku = ?", (sku,)
            ).fetchone()
            if item is None:
                raise NotFound(f"item {sku!r} not found")
            if item[0] < quantity:
                raise Conflict(f"insufficient stock for item {sku!r}")

            connection.execute(
                "UPDATE items SET available = available - ? WHERE sku = ?",
                (quantity, sku),
            )
            cursor = connection.execute(
                """
                INSERT INTO reservations(idempotency_key, sku, quantity, status)
                VALUES (?, ?, ?, 'active')
                """,
                (key, sku, quantity),
            )
            record = {
                "reservation_id": cursor.lastrowid,
                "idempotency_key": key,
                "sku": sku,
                "quantity": quantity,
                "status": "active",
            }
            connection.commit()
            return record
        except Exception:
            if connection.in_transaction:
                connection.rollback()
            raise
        finally:
            connection.close()

    def release(self, reservation_id):
        reservation_id = _positive_integer(reservation_id, "reservation id")
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
                raise NotFound(f"reservation {reservation_id} not found")

            if row[4] == "active":
                available = connection.execute(
                    "SELECT available FROM items WHERE sku = ?", (row[2],)
                ).fetchone()
                if available[0] > _MAX_SQLITE_INTEGER - row[3]:
                    raise ValueError("resulting quantity is too large")
                connection.execute(
                    "UPDATE items SET available = available + ? WHERE sku = ?",
                    (row[3], row[2]),
                )
                connection.execute(
                    "UPDATE reservations SET status = 'released' "
                    "WHERE reservation_id = ?",
                    (reservation_id,),
                )
                row = row[:4] + ("released",)

            connection.commit()
            return self._reservation_record(row)
        except Exception:
            if connection.in_transaction:
                connection.rollback()
            raise
        finally:
            connection.close()

    def report(self):
        connection = self._connect()
        try:
            # An explicit read transaction makes both parts of the report a
            # single consistent snapshot if a writer commits between queries.
            connection.execute("BEGIN")
            item_rows = connection.execute(
                "SELECT sku, available FROM items ORDER BY sku"
            ).fetchall()
            totals = connection.execute(
                """
                SELECT COUNT(*), COALESCE(SUM(quantity), 0)
                FROM reservations WHERE status = 'active'
                """
            ).fetchone()
            connection.commit()
            return {
                "items": [self._item_record(row) for row in item_rows],
                "active_reservations": totals[0],
                "reserved_units": totals[1],
            }
        except Exception:
            if connection.in_transaction:
                connection.rollback()
            raise
        finally:
            connection.close()
