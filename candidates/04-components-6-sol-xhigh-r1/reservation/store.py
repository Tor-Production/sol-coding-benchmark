"""SQLite-backed inventory and idempotent reservations."""

import sqlite3
from contextlib import closing


class Conflict(Exception):
    """The requested operation conflicts with current state."""


class NotFound(Exception):
    """The requested item or reservation does not exist."""


def _name(value, label):
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{label} must be a nonblank string")
    return value.strip()


def _positive_integer(value, label):
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise ValueError(f"{label} must be a positive integer")
    return value


def _reservation_record(row):
    return {
        "reservation_id": row[0],
        "idempotency_key": row[1],
        "sku": row[2],
        "quantity": int(row[3]),
        "status": row[4],
    }


class Store:
    def __init__(self, db_path):
        self.db_path = db_path
        with closing(self._connect()) as connection:
            with connection:
                connection.execute(
                    "CREATE TABLE IF NOT EXISTS items ("
                    "sku TEXT PRIMARY KEY, available TEXT NOT NULL)"
                )
                connection.execute(
                    "CREATE TABLE IF NOT EXISTS reservations ("
                    "id INTEGER PRIMARY KEY AUTOINCREMENT, "
                    "idempotency_key TEXT NOT NULL UNIQUE, "
                    "sku TEXT NOT NULL, quantity TEXT NOT NULL, "
                    "status TEXT NOT NULL CHECK (status IN ('active', 'released')))"
                )

    def _connect(self):
        # Each call owns a connection. SQLite waits for another writer to finish.
        return sqlite3.connect(self.db_path, timeout=30)

    def add_item(self, sku, quantity):
        sku = _name(sku, "sku")
        quantity = _positive_integer(quantity, "quantity")
        with closing(self._connect()) as connection:
            with connection:
                connection.execute("BEGIN IMMEDIATE")
                row = connection.execute(
                    "SELECT available FROM items WHERE sku = ?", (sku,)
                ).fetchone()
                available = int(row[0]) + quantity if row else quantity
                if row:
                    connection.execute(
                        "UPDATE items SET available = ? WHERE sku = ?",
                        (str(available), sku),
                    )
                else:
                    connection.execute(
                        "INSERT INTO items (sku, available) VALUES (?, ?)",
                        (sku, str(available)),
                    )
        return {"sku": sku, "available": available}

    def get_item(self, sku):
        sku = _name(sku, "sku")
        with closing(self._connect()) as connection:
            row = connection.execute(
                "SELECT available FROM items WHERE sku = ?", (sku,)
            ).fetchone()
        if row is None:
            raise NotFound(f"item {sku!r} not found")
        return {"sku": sku, "available": int(row[0])}

    def reserve(self, key, sku, quantity):
        key = _name(key, "idempotency_key")
        sku = _name(sku, "sku")
        quantity = _positive_integer(quantity, "quantity")
        with closing(self._connect()) as connection:
            with connection:
                connection.execute("BEGIN IMMEDIATE")
                previous = connection.execute(
                    "SELECT id, idempotency_key, sku, quantity, status "
                    "FROM reservations WHERE idempotency_key = ?", (key,)
                ).fetchone()
                if previous is not None:
                    record = _reservation_record(previous)
                    if record["sku"] != sku or record["quantity"] != quantity:
                        raise Conflict("idempotency key already used with different parameters")
                    # A retry replays the response from the original reserve call.
                    # Releasing the reservation does not create a new reservation.
                    record["status"] = "active"
                    return record

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
                    "INSERT INTO reservations "
                    "(idempotency_key, sku, quantity, status) "
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
        reservation_id = _positive_integer(reservation_id, "reservation_id")
        # SQLite rowids are signed 64-bit integers. Larger valid Python integers
        # cannot identify a row and must not overflow SQLite's parameter binder.
        if reservation_id > 2**63 - 1:
            raise NotFound(f"reservation {reservation_id} not found")
        with closing(self._connect()) as connection:
            with connection:
                connection.execute("BEGIN IMMEDIATE")
                row = connection.execute(
                    "SELECT id, idempotency_key, sku, quantity, status "
                    "FROM reservations WHERE id = ?", (reservation_id,)
                ).fetchone()
                if row is None:
                    raise NotFound(f"reservation {reservation_id} not found")
                record = _reservation_record(row)
                if record["status"] == "active":
                    item = connection.execute(
                        "SELECT available FROM items WHERE sku = ?", (record["sku"],)
                    ).fetchone()
                    connection.execute(
                        "UPDATE items SET available = ? WHERE sku = ?",
                        (str(int(item[0]) + record["quantity"]), record["sku"]),
                    )
                    connection.execute(
                        "UPDATE reservations SET status = 'released' WHERE id = ?",
                        (reservation_id,),
                    )
                    record["status"] = "released"
                return record

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
                active = [
                    int(row[0]) for row in connection.execute(
                        "SELECT quantity FROM reservations WHERE status = 'active'"
                    )
                ]
        return {
            "items": items,
            "active_reservations": len(active),
            "reserved_units": sum(active),
        }
