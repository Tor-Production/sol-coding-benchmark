"""SQLite-backed inventory and reservation storage."""

from contextlib import contextmanager
import sqlite3


class Conflict(Exception):
    """The requested operation conflicts with the current stored state."""


class NotFound(Exception):
    """The requested item or reservation does not exist."""


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
    if value > 9223372036854775807:
        raise ValueError(f"{name} is too large")
    return value


def _reservation_record(row):
    return {
        "reservation_id": row["reservation_id"],
        "idempotency_key": row["idempotency_key"],
        "sku": row["sku"],
        "quantity": row["quantity"],
        "status": row["status"],
    }


class Store:
    """Inventory store whose operations use short-lived SQLite connections."""

    def __init__(self, db_path):
        self.db_path = db_path
        self._initialize_schema()

    def _connect(self):
        connection = sqlite3.connect(self.db_path, timeout=30.0)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA busy_timeout = 30000")
        connection.execute("PRAGMA foreign_keys = ON")
        return connection

    def _initialize_schema(self):
        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
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
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    @contextmanager
    def _transaction(self, write=False):
        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE" if write else "BEGIN")
            yield connection
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def add_item(self, sku, quantity):
        sku = _nonblank_string(sku, "sku")
        quantity = _positive_integer(quantity, "quantity")

        with self._transaction(write=True) as connection:
            connection.execute(
                """
                INSERT INTO items (sku, available) VALUES (?, ?)
                ON CONFLICT(sku) DO UPDATE
                    SET available = items.available + excluded.available
                """,
                (sku, quantity),
            )
            row = connection.execute(
                "SELECT available FROM items WHERE sku = ?", (sku,)
            ).fetchone()
            return {"sku": sku, "available": row["available"]}

    def get_item(self, sku):
        sku = _nonblank_string(sku, "sku")

        with self._transaction() as connection:
            row = connection.execute(
                "SELECT available FROM items WHERE sku = ?", (sku,)
            ).fetchone()
            if row is None:
                raise NotFound(f"item not found: {sku}")
            return {"sku": sku, "available": row["available"]}

    def reserve(self, key, sku, quantity):
        key = _nonblank_string(key, "idempotency_key")
        sku = _nonblank_string(sku, "sku")
        quantity = _positive_integer(quantity, "quantity")

        with self._transaction(write=True) as connection:
            existing = connection.execute(
                """
                SELECT reservation_id, idempotency_key, sku, quantity, status
                FROM reservations WHERE idempotency_key = ?
                """,
                (key,),
            ).fetchone()
            if existing is not None:
                if existing["sku"] != sku or existing["quantity"] != quantity:
                    raise Conflict("idempotency key already used with different parameters")
                return _reservation_record(existing)

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
                INSERT INTO reservations
                    (idempotency_key, sku, quantity, status)
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
                FROM reservations WHERE reservation_id = ?
                """,
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
                    """
                    UPDATE reservations SET status = 'released'
                    WHERE reservation_id = ?
                    """,
                    (reservation_id,),
                )

            record = _reservation_record(row)
            record["status"] = "released"
            return record

    def report(self):
        with self._transaction() as connection:
            item_rows = connection.execute(
                "SELECT sku, available FROM items ORDER BY sku"
            ).fetchall()
            totals = connection.execute(
                """
                SELECT COUNT(*) AS active_reservations,
                       COALESCE(SUM(quantity), 0) AS reserved_units
                FROM reservations WHERE status = 'active'
                """
            ).fetchone()
            return {
                "items": [
                    {"sku": row["sku"], "available": row["available"]}
                    for row in item_rows
                ],
                "active_reservations": totals["active_reservations"],
                "reserved_units": totals["reserved_units"],
            }
