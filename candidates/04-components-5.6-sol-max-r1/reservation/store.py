"""SQLite-backed inventory and reservation storage."""

from contextlib import contextmanager, closing
import os
import sqlite3


class Conflict(Exception):
    """Raised when a request conflicts with current or recorded state."""


class NotFound(Exception):
    """Raised when an item or reservation does not exist."""


def _nonblank_string(value, name):
    if not isinstance(value, str):
        raise ValueError(f"{name} must be a nonblank string")
    value = value.strip()
    if not value:
        raise ValueError(f"{name} must be a nonblank string")
    return value


def _positive_integer(value, name):
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise ValueError(f"{name} must be a positive integer")
    return value


class Store:
    """An inventory store whose operations each use a short-lived connection."""

    def __init__(self, db_path):
        self._db_path = os.fspath(db_path)
        self._initialize_schema()

    def _connect(self):
        connection = sqlite3.connect(
            self._db_path,
            timeout=30.0,
            isolation_level=None,
        )
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA busy_timeout = 30000")
        return connection

    def _initialize_schema(self):
        with closing(self._connect()) as connection:
            # WAL permits reads during a short write transaction. The explicit
            # lock also serializes setup with constructors in other processes.
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
                connection.execute(
                    """
                    CREATE INDEX IF NOT EXISTS reservations_status_idx
                    ON reservations(status)
                    """
                )
            except BaseException:
                connection.rollback()
                raise
            else:
                connection.commit()

    @contextmanager
    def _transaction(self, *, write=False):
        with closing(self._connect()) as connection:
            connection.execute("BEGIN IMMEDIATE" if write else "BEGIN")
            try:
                yield connection
            except BaseException:
                connection.rollback()
                raise
            else:
                connection.commit()

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
        sku = _nonblank_string(sku, "sku")
        quantity = _positive_integer(quantity, "quantity")

        with self._transaction(write=True) as connection:
            connection.execute(
                """
                INSERT INTO items(sku, available) VALUES (?, ?)
                ON CONFLICT(sku) DO UPDATE
                SET available = items.available + excluded.available
                """,
                (sku, quantity),
            )
            row = connection.execute(
                "SELECT sku, available FROM items WHERE sku = ?", (sku,)
            ).fetchone()
            record = self._item_record(row)
        return record

    def get_item(self, sku):
        sku = _nonblank_string(sku, "sku")
        with closing(self._connect()) as connection:
            row = connection.execute(
                "SELECT sku, available FROM items WHERE sku = ?", (sku,)
            ).fetchone()
        if row is None:
            raise NotFound(f"item not found: {sku}")
        return self._item_record(row)

    def reserve(self, key, sku, quantity):
        key = _nonblank_string(key, "idempotency key")
        sku = _nonblank_string(sku, "sku")
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
                record = self._reservation_record(existing)
            else:
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
                    """
                    INSERT INTO reservations(
                        idempotency_key, sku, quantity, status
                    ) VALUES (?, ?, ?, 'active')
                    """,
                    (key, sku, quantity),
                )
                row = connection.execute(
                    """
                    SELECT reservation_id, idempotency_key, sku, quantity, status
                    FROM reservations
                    WHERE reservation_id = ?
                    """,
                    (cursor.lastrowid,),
                ).fetchone()
                record = self._reservation_record(row)
        return record

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

            if row["status"] == "active":
                changed = connection.execute(
                    """
                    UPDATE reservations SET status = 'released'
                    WHERE reservation_id = ? AND status = 'active'
                    """,
                    (reservation_id,),
                ).rowcount
                if changed:
                    connection.execute(
                        "UPDATE items SET available = available + ? WHERE sku = ?",
                        (row["quantity"], row["sku"]),
                    )

            result = connection.execute(
                """
                SELECT reservation_id, idempotency_key, sku, quantity, status
                FROM reservations
                WHERE reservation_id = ?
                """,
                (reservation_id,),
            ).fetchone()
            record = self._reservation_record(result)
        return record

    def report(self):
        with self._transaction() as connection:
            rows = connection.execute(
                "SELECT sku, available FROM items ORDER BY sku"
            ).fetchall()
            totals = connection.execute(
                """
                SELECT COUNT(*) AS active_reservations,
                       COALESCE(SUM(quantity), 0) AS reserved_units
                FROM reservations
                WHERE status = 'active'
                """
            ).fetchone()
            report = {
                "items": [self._item_record(row) for row in rows],
                "active_reservations": totals["active_reservations"],
                "reserved_units": totals["reserved_units"],
            }
        return report
