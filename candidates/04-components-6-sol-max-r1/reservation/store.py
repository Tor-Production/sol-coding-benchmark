"""SQLite-backed inventory and reservation operations."""

from contextlib import contextmanager
import os
import sqlite3
import uuid
import weakref


class Conflict(Exception):
    """The requested change conflicts with stock or an existing reservation."""


class NotFound(Exception):
    """The requested item or reservation does not exist."""


def _nonblank(value, name):
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} must be a nonblank string")
    value = value.strip()
    try:
        value.encode("utf-8")
    except UnicodeEncodeError as exc:
        raise ValueError(f"{name} must contain valid Unicode") from exc
    return value


def _positive_integer(value, name):
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise ValueError(f"{name} must be a positive integer")
    return value


class Store:
    def __init__(self, db_path):
        try:
            path = os.fspath(db_path)
        except TypeError as exc:
            raise ValueError("db_path must be a nonblank path") from exc
        if not isinstance(path, str) or not path.strip():
            raise ValueError("db_path must be a nonblank path")
        self._uri = path == ":memory:"
        if self._uri:
            # A keeper connection lets each method use and close its own
            # connection while retaining this Store's in-memory database.
            self.db_path = f"file:reservation-{uuid.uuid4().hex}?mode=memory&cache=shared"
            keeper = sqlite3.connect(self.db_path, uri=True, check_same_thread=False)
            self._keeper_finalizer = weakref.finalize(self, keeper.close)
        else:
            self.db_path = path
        with self._connection(write=True) as connection:
            connection.execute(
                """CREATE TABLE IF NOT EXISTS items (
                       sku TEXT PRIMARY KEY,
                       available TEXT NOT NULL
                   )"""
            )
            connection.execute(
                """CREATE TABLE IF NOT EXISTS reservations (
                       reservation_id INTEGER PRIMARY KEY AUTOINCREMENT,
                       idempotency_key TEXT NOT NULL UNIQUE,
                       sku TEXT NOT NULL,
                       quantity TEXT NOT NULL,
                       status TEXT NOT NULL CHECK (status IN ('active', 'released')),
                       FOREIGN KEY (sku) REFERENCES items(sku)
                   )"""
            )

    @contextmanager
    def _connection(self, write=False):
        connection = sqlite3.connect(self.db_path, timeout=30, uri=self._uri)
        try:
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
        with self._connection(write=True) as connection:
            row = connection.execute(
                "SELECT available FROM items WHERE sku = ?", (sku,)
            ).fetchone()
            available = (int(row[0]) if row else 0) + quantity
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
        with self._connection() as connection:
            row = connection.execute(
                "SELECT available FROM items WHERE sku = ?", (sku,)
            ).fetchone()
        if row is None:
            raise NotFound(f"item {sku!r} not found")
        return {"sku": sku, "available": int(row[0])}

    @staticmethod
    def _reservation_record(row):
        return {
            "reservation_id": row[0],
            "idempotency_key": row[1],
            "sku": row[2],
            "quantity": int(row[3]),
            "status": row[4],
        }

    def reserve(self, key, sku, quantity):
        key = _nonblank(key, "idempotency_key")
        sku = _nonblank(sku, "sku")
        quantity = _positive_integer(quantity, "quantity")
        with self._connection(write=True) as connection:
            existing = connection.execute(
                """SELECT reservation_id, idempotency_key, sku, quantity, status
                   FROM reservations WHERE idempotency_key = ?""",
                (key,),
            ).fetchone()
            if existing is not None:
                record = self._reservation_record(existing)
                if record["sku"] != sku or record["quantity"] != quantity:
                    raise Conflict(f"idempotency key {key!r} has different parameters")
                return record

            row = connection.execute(
                "SELECT available FROM items WHERE sku = ?", (sku,)
            ).fetchone()
            if row is None:
                raise NotFound(f"item {sku!r} not found")
            available = int(row[0])
            if available < quantity:
                raise Conflict(f"insufficient stock for item {sku!r}")

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
        with self._connection(write=True) as connection:
            row = connection.execute(
                """SELECT reservation_id, idempotency_key, sku, quantity, status
                   FROM reservations WHERE reservation_id = ?""",
                (str(reservation_id),),
            ).fetchone()
            if row is None:
                raise NotFound(f"reservation {reservation_id} not found")
            record = self._reservation_record(row)
            if record["status"] == "active":
                item = connection.execute(
                    "SELECT available FROM items WHERE sku = ?", (record["sku"],)
                ).fetchone()
                connection.execute(
                    "UPDATE items SET available = ? WHERE sku = ?",
                    (str(int(item[0]) + record["quantity"]), record["sku"]),
                )
                connection.execute(
                    "UPDATE reservations SET status = 'released' WHERE reservation_id = ?",
                    (reservation_id,),
                )
                record["status"] = "released"
            return record

    def report(self):
        with self._connection() as connection:
            items = connection.execute(
                "SELECT sku, available FROM items ORDER BY sku"
            ).fetchall()
            active = connection.execute(
                "SELECT quantity FROM reservations WHERE status = 'active'"
            ).fetchall()
        return {
            "items": [
                {"sku": sku, "available": int(available)}
                for sku, available in items
            ],
            "active_reservations": len(active),
            "reserved_units": sum(int(row[0]) for row in active),
        }
