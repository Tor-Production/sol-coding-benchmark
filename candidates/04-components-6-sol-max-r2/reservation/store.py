"""SQLite-backed inventory and reservation operations."""

import sqlite3
import uuid
import weakref
from contextlib import closing, contextmanager


_MAX_SQLITE_INT = 2**63 - 1


class Conflict(Exception):
    """The requested change conflicts with existing inventory or a key."""


class NotFound(Exception):
    """An item or reservation does not exist."""


def _nonblank(value, name):
    if not isinstance(value, str):
        raise ValueError(f"{name} must be a nonblank string")
    value = value.strip()
    if not value:
        raise ValueError(f"{name} must be a nonblank string")
    try:
        value.encode("utf-8")
    except UnicodeEncodeError as exc:
        raise ValueError(f"{name} must be valid UTF-8") from exc
    return value


def _positive_int(value, name):
    if (
        not isinstance(value, int)
        or isinstance(value, bool)
        or value <= 0
        or value > _MAX_SQLITE_INT
    ):
        raise ValueError(f"{name} must be a positive integer")
    return value


def _reservation_record(row, status=None):
    return {
        "reservation_id": row["reservation_id"],
        "idempotency_key": row["idempotency_key"],
        "sku": row["sku"],
        "quantity": row["quantity"],
        "status": row["status"] if status is None else status,
    }


class Store:
    def __init__(self, db_path):
        self._uri = str(db_path) == ":memory:"
        if self._uri:
            # Separate connections to plain :memory: each see a different DB.
            # Keep one shared-memory connection alive for this Store's lifetime
            # while still closing every connection used by public methods.
            self.db_path = f"file:reservation-{uuid.uuid4().hex}?mode=memory&cache=shared"
            keeper = sqlite3.connect(self.db_path, uri=True, check_same_thread=False)
            weakref.finalize(self, keeper.close)
        else:
            self.db_path = db_path
        with self._transaction(write=True) as connection:
            connection.execute(
                """CREATE TABLE IF NOT EXISTS items (
                    sku TEXT PRIMARY KEY,
                    available INTEGER NOT NULL
                        CHECK (typeof(available) = 'integer' AND available >= 0)
                )"""
            )
            connection.execute(
                """CREATE TABLE IF NOT EXISTS reservations (
                    reservation_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    idempotency_key TEXT NOT NULL UNIQUE,
                    sku TEXT NOT NULL REFERENCES items(sku),
                    quantity INTEGER NOT NULL CHECK (quantity > 0),
                    status TEXT NOT NULL CHECK (status IN ('active', 'released'))
                )"""
            )
            connection.execute(
                "CREATE INDEX IF NOT EXISTS reservations_sku_status "
                "ON reservations(sku, status)"
            )

    @contextmanager
    def _transaction(self, write=False):
        # Each call owns and closes its connection. An immediate write lock makes
        # the stock check and subsequent update indivisible across Store objects.
        with closing(
            sqlite3.connect(
                self.db_path, timeout=30, isolation_level=None, uri=self._uri
            )
        ) as connection:
            connection.row_factory = sqlite3.Row
            connection.execute("PRAGMA foreign_keys = ON")
            connection.execute("BEGIN IMMEDIATE" if write else "BEGIN")
            try:
                yield connection
                connection.commit()
            except BaseException:
                connection.rollback()
                raise

    def add_item(self, sku, quantity):
        sku = _nonblank(sku, "sku")
        quantity = _positive_int(quantity, "quantity")
        with self._transaction(write=True) as connection:
            row = connection.execute(
                "SELECT available FROM items WHERE sku = ?", (sku,)
            ).fetchone()
            available = 0 if row is None else row["available"]
            # Include active reservations so a later release can always restore
            # its units without overflowing SQLite's signed integer range.
            active_units = sum(
                reservation["quantity"]
                for reservation in connection.execute(
                    "SELECT quantity FROM reservations WHERE sku = ? AND status = 'active'",
                    (sku,),
                )
            )
            if available + active_units + quantity > _MAX_SQLITE_INT:
                raise Conflict("item quantity exceeds SQLite integer capacity")
            new_available = available + quantity
            if row is None:
                connection.execute(
                    "INSERT INTO items (sku, available) VALUES (?, ?)",
                    (sku, new_available),
                )
            else:
                connection.execute(
                    "UPDATE items SET available = ? WHERE sku = ?",
                    (new_available, sku),
                )
            return {"sku": sku, "available": new_available}

    def get_item(self, sku):
        sku = _nonblank(sku, "sku")
        with self._transaction() as connection:
            row = connection.execute(
                "SELECT available FROM items WHERE sku = ?", (sku,)
            ).fetchone()
            if row is None:
                raise NotFound(f"item {sku!r} not found")
            return {"sku": sku, "available": row["available"]}

    def reserve(self, key, sku, quantity):
        key = _nonblank(key, "idempotency_key")
        sku = _nonblank(sku, "sku")
        quantity = _positive_int(quantity, "quantity")
        with self._transaction(write=True) as connection:
            existing = connection.execute(
                "SELECT * FROM reservations WHERE idempotency_key = ?", (key,)
            ).fetchone()
            if existing is not None:
                if existing["sku"] != sku or existing["quantity"] != quantity:
                    raise Conflict("idempotency key already used with different parameters")
                # The reserve request originally returned an active record. A
                # retry returns that same result even if it was later released.
                return _reservation_record(existing, status="active")

            item = connection.execute(
                "SELECT available FROM items WHERE sku = ?", (sku,)
            ).fetchone()
            if item is None:
                raise NotFound(f"item {sku!r} not found")
            if item["available"] < quantity:
                raise Conflict(f"insufficient stock for {sku!r}")

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
            return {
                "reservation_id": cursor.lastrowid,
                "idempotency_key": key,
                "sku": sku,
                "quantity": quantity,
                "status": "active",
            }

    def release(self, reservation_id):
        reservation_id = _positive_int(reservation_id, "reservation_id")
        with self._transaction(write=True) as connection:
            row = connection.execute(
                "SELECT * FROM reservations WHERE reservation_id = ?",
                (reservation_id,),
            ).fetchone()
            if row is None:
                raise NotFound(f"reservation {reservation_id} not found")
            if row["status"] == "active":
                connection.execute(
                    "UPDATE items SET available = available + ? WHERE sku = ?",
                    (row["quantity"], row["sku"]),
                )
                connection.execute(
                    "UPDATE reservations SET status = 'released' WHERE reservation_id = ?",
                    (reservation_id,),
                )
            return _reservation_record(row, status="released")

    def report(self):
        with self._transaction() as connection:
            items = [
                {"sku": row["sku"], "available": row["available"]}
                for row in connection.execute(
                    "SELECT sku, available FROM items ORDER BY sku"
                )
            ]
            quantities = [
                row["quantity"]
                for row in connection.execute(
                    "SELECT quantity FROM reservations WHERE status = 'active'"
                )
            ]
            return {
                "items": items,
                "active_reservations": len(quantities),
                "reserved_units": sum(quantities),
            }
