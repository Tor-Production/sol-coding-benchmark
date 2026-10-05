"""SQLite-backed inventory and reservation storage."""

from contextlib import contextmanager
import os
import sqlite3


class Conflict(Exception):
    """The requested operation conflicts with the current stored state."""


class NotFound(Exception):
    """A requested item or reservation does not exist."""


_MAX_SQLITE_INTEGER = (1 << 63) - 1


def _clean_text(value, name):
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
    """Inventory store whose public operations use short-lived connections."""

    def __init__(self, db_path):
        try:
            self._db_path = os.fspath(db_path)
        except TypeError as exc:
            raise ValueError("db_path must be a filesystem path") from exc
        if self._db_path in ("", b""):
            raise ValueError("db_path must not be empty")
        self._initialize_schema()

    def _connect(self):
        connection = sqlite3.connect(
            self._db_path,
            timeout=30.0,
            isolation_level=None,
        )
        try:
            connection.row_factory = sqlite3.Row
            connection.execute("PRAGMA foreign_keys = ON")
            connection.execute("PRAGMA busy_timeout = 30000")
            return connection
        except BaseException:
            connection.close()
            raise

    @contextmanager
    def _transaction(self, *, write=False):
        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE" if write else "BEGIN")
            yield connection
            connection.commit()
        except BaseException:
            if connection.in_transaction:
                connection.rollback()
            raise
        finally:
            connection.close()

    def _initialize_schema(self):
        with self._transaction(write=True) as connection:
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
            connection.execute(
                """
                CREATE INDEX IF NOT EXISTS reservations_status_idx
                ON reservations(status)
                """
            )

    @staticmethod
    def _item_record(row):
        return {"sku": row["sku"], "available": row["available"]}

    @staticmethod
    def _reservation_record(row):
        return {
            "reservation_id": row["reservation_id"],
            "idempotency_key": row["idempotency_key"],
            "sku": row["sku"],
            "quantity": row["quantity"],
            "status": row["status"],
        }

    def add_item(self, sku, quantity):
        sku = _clean_text(sku, "sku")
        quantity = _positive_integer(quantity, "quantity")

        with self._transaction(write=True) as connection:
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
                available = row["available"] + quantity
                if available > _MAX_SQLITE_INTEGER:
                    raise ValueError("resulting available quantity is too large")
                connection.execute(
                    "UPDATE items SET available = ? WHERE sku = ?",
                    (available, sku),
                )
            return {"sku": sku, "available": available}

    def get_item(self, sku):
        sku = _clean_text(sku, "sku")
        with self._transaction() as connection:
            row = connection.execute(
                "SELECT sku, available FROM items WHERE sku = ?", (sku,)
            ).fetchone()
            if row is None:
                raise NotFound(f"item not found: {sku}")
            return self._item_record(row)

    def reserve(self, key, sku, quantity):
        key = _clean_text(key, "idempotency key")
        sku = _clean_text(sku, "sku")
        quantity = _positive_integer(quantity, "quantity")

        with self._transaction(write=True) as connection:
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
                return self._reservation_record(existing)

            item = connection.execute(
                "SELECT available FROM items WHERE sku = ?", (sku,)
            ).fetchone()
            if item is None:
                raise NotFound(f"item not found: {sku}")
            if item["available"] < quantity:
                raise Conflict(f"insufficient stock for item: {sku}")

            connection.execute(
                "UPDATE items SET available = ? WHERE sku = ?",
                (item["available"] - quantity, sku),
            )
            cursor = connection.execute(
                """
                INSERT INTO reservations(idempotency_key, sku, quantity, status)
                VALUES (?, ?, ?, 'active')
                """,
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
        reservation_id = _positive_integer(reservation_id, "reservation_id")

        with self._transaction(write=True) as connection:
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
            if row["status"] == "released":
                return self._reservation_record(row)

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
            return {
                "reservation_id": row["reservation_id"],
                "idempotency_key": row["idempotency_key"],
                "sku": row["sku"],
                "quantity": row["quantity"],
                "status": "released",
            }

    def report(self):
        with self._transaction() as connection:
            item_rows = connection.execute(
                "SELECT sku, available FROM items ORDER BY sku"
            ).fetchall()
            active_rows = connection.execute(
                "SELECT quantity FROM reservations WHERE status = 'active'"
            ).fetchall()
            return {
                "items": [self._item_record(row) for row in item_rows],
                "active_reservations": len(active_rows),
                "reserved_units": sum(row["quantity"] for row in active_rows),
            }
