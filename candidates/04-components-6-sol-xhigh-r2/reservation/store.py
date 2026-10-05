"""SQLite-backed inventory and reservation operations."""

import os
import sqlite3
import uuid
from contextlib import closing


class Conflict(Exception):
    """An operation conflicts with stock or an existing idempotency key."""


class NotFound(Exception):
    """A requested item or reservation does not exist."""


def _nonblank(value, name):
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} must be a nonblank string")
    return value.strip()


def _positive_int(value, name):
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise ValueError(f"{name} must be a positive integer")
    return value


def _reservation(row, status=None):
    return {
        "reservation_id": row[0],
        "idempotency_key": row[1],
        "sku": row[2],
        "quantity": int(row[3]),
        "status": row[4] if status is None else status,
    }


class Store:
    def __init__(self, db_path):
        try:
            db_path = os.fspath(db_path)
        except TypeError as exc:
            raise ValueError("db_path must be a nonblank filesystem path") from exc
        if not db_path or not db_path.strip():
            raise ValueError("db_path must be a nonblank filesystem path")
        self._uri = db_path == ":memory:"
        self._keeper = None
        if self._uri:
            # Keep a shared in-memory database alive while each method closes
            # its own short-lived connection.
            db_path = f"file:reservation-{uuid.uuid4().hex}?mode=memory&cache=shared"
            self._keeper = sqlite3.connect(
                db_path, uri=True, timeout=30, check_same_thread=False
            )
        self.db_path = db_path
        try:
            with closing(self._connect()) as connection:
                with connection:
                    connection.execute("BEGIN IMMEDIATE")
                    connection.execute(
                        "CREATE TABLE IF NOT EXISTS items ("
                        "sku TEXT PRIMARY KEY, "
                        "available TEXT NOT NULL)"
                    )
                    connection.execute(
                        "CREATE TABLE IF NOT EXISTS reservations ("
                        "reservation_id INTEGER PRIMARY KEY AUTOINCREMENT, "
                        "idempotency_key TEXT NOT NULL UNIQUE, "
                        "sku TEXT NOT NULL REFERENCES items(sku), "
                        "quantity TEXT NOT NULL, "
                        "status TEXT NOT NULL CHECK (status IN ('active', 'released')))"
                    )
        except Exception:
            if self._keeper is not None:
                self._keeper.close()
                self._keeper = None
            raise

    def __del__(self):
        keeper = getattr(self, "_keeper", None)
        if keeper is not None:
            keeper.close()

    def _connect(self):
        connection = sqlite3.connect(self.db_path, timeout=30, uri=self._uri)
        connection.execute("PRAGMA foreign_keys = ON")
        return connection

    def add_item(self, sku, quantity):
        sku = _nonblank(sku, "sku")
        quantity = _positive_int(quantity, "quantity")
        with closing(self._connect()) as connection:
            with connection:
                connection.execute("BEGIN IMMEDIATE")
                row = connection.execute(
                    "SELECT available FROM items WHERE sku = ?", (sku,)
                ).fetchone()
                if row is None:
                    available = quantity
                    connection.execute(
                        "INSERT INTO items (sku, available) VALUES (?, ?)",
                        (sku, str(available)),
                    )
                else:
                    available = int(row[0]) + quantity
                    connection.execute(
                        "UPDATE items SET available = ? WHERE sku = ?",
                        (str(available), sku),
                    )
        return {"sku": sku, "available": available}

    def get_item(self, sku):
        sku = _nonblank(sku, "sku")
        with closing(self._connect()) as connection:
            row = connection.execute(
                "SELECT available FROM items WHERE sku = ?", (sku,)
            ).fetchone()
        if row is None:
            raise NotFound(f"item {sku!r} not found")
        return {"sku": sku, "available": int(row[0])}

    def reserve(self, key, sku, quantity):
        key = _nonblank(key, "idempotency_key")
        sku = _nonblank(sku, "sku")
        quantity = _positive_int(quantity, "quantity")
        with closing(self._connect()) as connection:
            with connection:
                connection.execute("BEGIN IMMEDIATE")
                existing = connection.execute(
                    "SELECT reservation_id, idempotency_key, sku, quantity, status "
                    "FROM reservations WHERE idempotency_key = ?",
                    (key,),
                ).fetchone()
                if existing is not None:
                    if existing[2] != sku or int(existing[3]) != quantity:
                        raise Conflict(f"idempotency key {key!r} has different parameters")
                    # Replay the original creation response after release, too.
                    return _reservation(existing, status="active")

                item = connection.execute(
                    "SELECT available FROM items WHERE sku = ?", (sku,)
                ).fetchone()
                if item is None:
                    raise NotFound(f"item {sku!r} not found")
                available = int(item[0])
                if available < quantity:
                    raise Conflict(f"insufficient stock for item {sku!r}")
                connection.execute(
                    "UPDATE items SET available = ? WHERE sku = ?",
                    (str(available - quantity), sku),
                )
                cursor = connection.execute(
                    "INSERT INTO reservations (idempotency_key, sku, quantity, status) "
                    "VALUES (?, ?, ?, 'active')",
                    (key, sku, str(quantity)),
                )
                reservation_id = cursor.lastrowid
        return {
            "reservation_id": reservation_id,
            "idempotency_key": key,
            "sku": sku,
            "quantity": quantity,
            "status": "active",
        }

    def release(self, reservation_id):
        reservation_id = _positive_int(reservation_id, "reservation_id")
        if reservation_id > 2**63 - 1:
            raise NotFound(f"reservation {reservation_id} not found")
        with closing(self._connect()) as connection:
            with connection:
                connection.execute("BEGIN IMMEDIATE")
                row = connection.execute(
                    "SELECT reservation_id, idempotency_key, sku, quantity, status "
                    "FROM reservations WHERE reservation_id = ?",
                    (reservation_id,),
                ).fetchone()
                if row is None:
                    raise NotFound(f"reservation {reservation_id} not found")
                if row[4] == "active":
                    available = int(connection.execute(
                        "SELECT available FROM items WHERE sku = ?", (row[2],)
                    ).fetchone()[0])
                    connection.execute(
                        "UPDATE items SET available = ? WHERE sku = ?",
                        (str(available + int(row[3])), row[2]),
                    )
                    connection.execute(
                        "UPDATE reservations SET status = 'released' "
                        "WHERE reservation_id = ?",
                        (reservation_id,),
                    )
        return _reservation(row, status="released")

    def report(self):
        with closing(self._connect()) as connection:
            with connection:
                connection.execute("BEGIN")
                items = [
                    {"sku": sku, "available": int(available)}
                    for sku, available in connection.execute(
                        "SELECT sku, available FROM items ORDER BY sku"
                    )
                ]
                active = 0
                units = 0
                for (stored_quantity,) in connection.execute(
                    "SELECT quantity FROM reservations WHERE status = 'active'"
                ):
                    active += 1
                    units += int(stored_quantity)
        return {
            "items": items,
            "active_reservations": active,
            "reserved_units": units,
        }
