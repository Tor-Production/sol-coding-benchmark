"""SQLite-backed inventory and reservations."""

import sqlite3
from contextlib import closing, contextmanager


class Conflict(Exception):
    """A reservation conflicts with its key or with available stock."""


class NotFound(Exception):
    """An item or reservation does not exist."""


def _nonblank(value, name):
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} must be a nonblank string")
    return value.strip()


def _positive_integer(value, name):
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise ValueError(f"{name} must be a positive integer")
    return int(value)


def _reservation_record(row):
    return {
        "reservation_id": row[0],
        "idempotency_key": row[1],
        "sku": row[2],
        "quantity": int(row[3]),
        "status": "active",
    }


class Store:
    def __init__(self, db_path):
        self.db_path = db_path
        with self._transaction(write=True) as connection:
            # Decimal text preserves Python integers beyond SQLite's signed
            # 64-bit INTEGER range; all arithmetic happens under a write lock.
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
                    sku TEXT NOT NULL REFERENCES items(sku),
                    quantity TEXT NOT NULL,
                    status TEXT NOT NULL CHECK (status IN ('active', 'released'))
                )"""
            )

    @contextmanager
    def _transaction(self, write=False):
        # A connection is deliberately scoped to each call, including failures.
        with closing(sqlite3.connect(self.db_path, timeout=30)) as connection:
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
        quantity = _positive_integer(quantity, "quantity")
        with self._transaction(write=True) as connection:
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
        with self._transaction() as connection:
            row = connection.execute(
                "SELECT available FROM items WHERE sku = ?", (sku,)
            ).fetchone()
        if row is None:
            raise NotFound(f"item {sku!r} not found")
        return {"sku": sku, "available": int(row[0])}

    def reserve(self, key, sku, quantity):
        key = _nonblank(key, "idempotency_key")
        sku = _nonblank(sku, "sku")
        quantity = _positive_integer(quantity, "quantity")
        with self._transaction(write=True) as connection:
            previous = connection.execute(
                """SELECT reservation_id, idempotency_key, sku, quantity
                   FROM reservations WHERE idempotency_key = ?""",
                (key,),
            ).fetchone()
            if previous is not None:
                if previous[2] != sku or int(previous[3]) != quantity:
                    raise Conflict("idempotency key already used with different parameters")
                # Replay the original reserve response, even after release.
                return _reservation_record(previous)

            item = connection.execute(
                "SELECT available FROM items WHERE sku = ?", (sku,)
            ).fetchone()
            if item is None:
                raise NotFound(f"item {sku!r} not found")
            available = int(item[0])
            if available < quantity:
                raise Conflict("insufficient stock")

            connection.execute(
                "UPDATE items SET available = ? WHERE sku = ?",
                (str(available - quantity), sku),
            )
            cursor = connection.execute(
                """INSERT INTO reservations (idempotency_key, sku, quantity, status)
                   VALUES (?, ?, ?, 'active')""",
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
        reservation_id = _positive_integer(reservation_id, "reservation_id")
        if reservation_id > 2**63 - 1:
            raise NotFound(f"reservation {reservation_id} not found")
        with self._transaction(write=True) as connection:
            row = connection.execute(
                """SELECT reservation_id, idempotency_key, sku, quantity, status
                   FROM reservations WHERE reservation_id = ?""",
                (reservation_id,),
            ).fetchone()
            if row is None:
                raise NotFound(f"reservation {reservation_id} not found")
            if row[4] == "active":
                item = connection.execute(
                    "SELECT available FROM items WHERE sku = ?", (row[2],)
                ).fetchone()
                available = int(item[0]) + int(row[3])
                connection.execute(
                    "UPDATE items SET available = ? WHERE sku = ?",
                    (str(available), row[2]),
                )
                connection.execute(
                    "UPDATE reservations SET status = 'released' WHERE reservation_id = ?",
                    (reservation_id,),
                )
            return {
                "reservation_id": row[0],
                "idempotency_key": row[1],
                "sku": row[2],
                "quantity": int(row[3]),
                "status": "released",
            }

    def report(self):
        with self._transaction() as connection:
            items = [
                {"sku": sku, "available": int(available)}
                for sku, available in connection.execute(
                    "SELECT sku, available FROM items ORDER BY sku"
                )
            ]
            count = units = 0
            for (quantity,) in connection.execute(
                "SELECT quantity FROM reservations WHERE status = 'active'"
            ):
                count += 1
                units += int(quantity)
        return {
            "items": items,
            "active_reservations": count,
            "reserved_units": units,
        }
