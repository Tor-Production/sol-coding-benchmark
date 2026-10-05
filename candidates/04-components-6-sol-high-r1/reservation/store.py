"""SQLite-backed inventory and reservation operations."""

from contextlib import closing
import sqlite3


class Conflict(Exception):
    """The requested operation conflicts with stock or an existing key."""


class NotFound(Exception):
    """The requested item or reservation does not exist."""


def _name(value, label):
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{label} must be a nonblank string")
    return value.strip()


def _positive_integer(value, label):
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise ValueError(f"{label} must be a positive integer")
    if value > 2**63 - 1:
        raise ValueError(f"{label} is too large")
    return value


def _reservation(row):
    return {
        "reservation_id": row["id"],
        "idempotency_key": row["idempotency_key"],
        "sku": row["sku"],
        "quantity": row["quantity"],
        "status": row["status"],
    }


class Store:
    def __init__(self, db_path):
        self.db_path = db_path
        with closing(self._connect()) as connection:
            with connection:
                connection.execute(
                    "CREATE TABLE IF NOT EXISTS items ("
                    "sku TEXT PRIMARY KEY, available INTEGER NOT NULL CHECK (available >= 0))"
                )
                connection.execute(
                    "CREATE TABLE IF NOT EXISTS reservations ("
                    "id INTEGER PRIMARY KEY, "
                    "idempotency_key TEXT NOT NULL UNIQUE, "
                    "sku TEXT NOT NULL REFERENCES items(sku), "
                    "quantity INTEGER NOT NULL CHECK (quantity > 0), "
                    "status TEXT NOT NULL CHECK (status IN ('active', 'released')))"
                )

    def _connect(self):
        connection = sqlite3.connect(self.db_path, timeout=30)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        return connection

    def add_item(self, sku, quantity):
        sku = _name(sku, "sku")
        quantity = _positive_integer(quantity, "quantity")
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
                        (sku, available),
                    )
                else:
                    available = row["available"] + quantity
                    if available > 2**63 - 1:
                        raise ValueError("available quantity is too large")
                    connection.execute(
                        "UPDATE items SET available = ? WHERE sku = ?",
                        (available, sku),
                    )
                return {"sku": sku, "available": available}

    def get_item(self, sku):
        sku = _name(sku, "sku")
        with closing(self._connect()) as connection:
            row = connection.execute(
                "SELECT sku, available FROM items WHERE sku = ?", (sku,)
            ).fetchone()
        if row is None:
            raise NotFound(f"item {sku!r} not found")
        return {"sku": row["sku"], "available": row["available"]}

    def reserve(self, key, sku, quantity):
        key = _name(key, "idempotency_key")
        sku = _name(sku, "sku")
        quantity = _positive_integer(quantity, "quantity")
        with closing(self._connect()) as connection:
            with connection:
                connection.execute("BEGIN IMMEDIATE")
                existing = connection.execute(
                    "SELECT * FROM reservations WHERE idempotency_key = ?", (key,)
                ).fetchone()
                if existing is not None:
                    if existing["sku"] != sku or existing["quantity"] != quantity:
                        raise Conflict("idempotency key already used for different parameters")
                    original = _reservation(existing)
                    original["status"] = "active"
                    return original

                item = connection.execute(
                    "SELECT available FROM items WHERE sku = ?", (sku,)
                ).fetchone()
                if item is None:
                    raise NotFound(f"item {sku!r} not found")
                if item["available"] < quantity:
                    raise Conflict("insufficient stock")

                connection.execute(
                    "UPDATE items SET available = available - ? WHERE sku = ?",
                    (quantity, sku),
                )
                cursor = connection.execute(
                    "INSERT INTO reservations (idempotency_key, sku, quantity, status) "
                    "VALUES (?, ?, ?, 'active')",
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
        with closing(self._connect()) as connection:
            with connection:
                connection.execute("BEGIN IMMEDIATE")
                row = connection.execute(
                    "SELECT * FROM reservations WHERE id = ?", (reservation_id,)
                ).fetchone()
                if row is None:
                    raise NotFound(f"reservation {reservation_id} not found")
                if row["status"] == "active":
                    connection.execute(
                        "UPDATE reservations SET status = 'released' WHERE id = ?",
                        (reservation_id,),
                    )
                    connection.execute(
                        "UPDATE items SET available = available + ? WHERE sku = ?",
                        (row["quantity"], row["sku"]),
                    )
                result = _reservation(row)
                result["status"] = "released"
                return result

    def report(self):
        with closing(self._connect()) as connection:
            with connection:
                connection.execute("BEGIN")
                items = [
                    {"sku": row["sku"], "available": row["available"]}
                    for row in connection.execute(
                        "SELECT sku, available FROM items ORDER BY sku"
                    )
                ]
                totals = connection.execute(
                    "SELECT COUNT(*) AS count, COALESCE(SUM(quantity), 0) AS units "
                    "FROM reservations WHERE status = 'active'"
                ).fetchone()
                return {
                    "items": items,
                    "active_reservations": totals["count"],
                    "reserved_units": totals["units"],
                }
