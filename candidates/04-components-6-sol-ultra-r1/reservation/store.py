"""SQLite-backed inventory and reservation operations."""

from contextlib import contextmanager
import sqlite3


class Conflict(Exception):
    """The requested change conflicts with stock or an existing key."""


class NotFound(Exception):
    """The requested item or reservation does not exist."""


def _nonblank(value, name):
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} must be a nonblank string")
    return value.strip()


def _positive_int(value, name):
    if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
        raise ValueError(f"{name} must be a positive integer")
    return value


def _reservation(row):
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
        with self._transaction(write=True) as conn:
            conn.execute(
                "CREATE TABLE IF NOT EXISTS items ("
                "sku TEXT PRIMARY KEY, available TEXT NOT NULL)"
            )
            conn.execute(
                "CREATE TABLE IF NOT EXISTS reservations ("
                "reservation_id INTEGER PRIMARY KEY AUTOINCREMENT, "
                "idempotency_key TEXT NOT NULL UNIQUE, "
                "sku TEXT NOT NULL, quantity TEXT NOT NULL, "
                "status TEXT NOT NULL CHECK (status IN ('active', 'released')))"
            )

    @contextmanager
    def _transaction(self, write=False):
        # Each call owns and closes its connection. BEGIN IMMEDIATE serializes
        # writers before they read stock or idempotency keys.
        conn = sqlite3.connect(self.db_path, timeout=30, isolation_level=None)
        try:
            conn.execute("BEGIN IMMEDIATE" if write else "BEGIN")
            yield conn
            conn.execute("COMMIT")
        except BaseException:
            if conn.in_transaction:
                conn.execute("ROLLBACK")
            raise
        finally:
            conn.close()

    def add_item(self, sku, quantity):
        sku = _nonblank(sku, "sku")
        quantity = _positive_int(quantity, "quantity")
        with self._transaction(write=True) as conn:
            row = conn.execute(
                "SELECT available FROM items WHERE sku = ?", (sku,)
            ).fetchone()
            available = (int(row[0]) if row else 0) + quantity
            if row:
                conn.execute(
                    "UPDATE items SET available = ? WHERE sku = ?",
                    (str(available), sku),
                )
            else:
                conn.execute(
                    "INSERT INTO items (sku, available) VALUES (?, ?)",
                    (sku, str(available)),
                )
        return {"sku": sku, "available": available}

    def get_item(self, sku):
        sku = _nonblank(sku, "sku")
        with self._transaction() as conn:
            row = conn.execute(
                "SELECT available FROM items WHERE sku = ?", (sku,)
            ).fetchone()
        if row is None:
            raise NotFound(f"item {sku!r} not found")
        return {"sku": sku, "available": int(row[0])}

    def reserve(self, key, sku, quantity):
        key = _nonblank(key, "idempotency_key")
        sku = _nonblank(sku, "sku")
        quantity = _positive_int(quantity, "quantity")
        with self._transaction(write=True) as conn:
            row = conn.execute(
                "SELECT reservation_id, idempotency_key, sku, quantity, status "
                "FROM reservations WHERE idempotency_key = ?",
                (key,),
            ).fetchone()
            if row is not None:
                record = _reservation(row)
                if record["sku"] != sku or record["quantity"] != quantity:
                    raise Conflict(f"idempotency key {key!r} has different parameters")
                return record

            item = conn.execute(
                "SELECT available FROM items WHERE sku = ?", (sku,)
            ).fetchone()
            if item is None:
                raise NotFound(f"item {sku!r} not found")
            available = int(item[0])
            if available < quantity:
                raise Conflict(f"insufficient stock for item {sku!r}")

            cursor = conn.execute(
                "INSERT INTO reservations (idempotency_key, sku, quantity, status) "
                "VALUES (?, ?, ?, 'active')",
                (key, sku, str(quantity)),
            )
            conn.execute(
                "UPDATE items SET available = ? WHERE sku = ?",
                (str(available - quantity), sku),
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
        # SQLite row IDs are signed 64-bit integers. Larger positive IDs are
        # valid input but cannot name a stored reservation.
        if reservation_id > 2**63 - 1:
            raise NotFound(f"reservation {reservation_id} not found")
        with self._transaction(write=True) as conn:
            row = conn.execute(
                "SELECT reservation_id, idempotency_key, sku, quantity, status "
                "FROM reservations WHERE reservation_id = ?",
                (reservation_id,),
            ).fetchone()
            if row is None:
                raise NotFound(f"reservation {reservation_id} not found")
            record = _reservation(row)
            if record["status"] == "active":
                item = conn.execute(
                    "SELECT available FROM items WHERE sku = ?", (record["sku"],)
                ).fetchone()
                conn.execute(
                    "UPDATE items SET available = ? WHERE sku = ?",
                    (str(int(item[0]) + record["quantity"]), record["sku"]),
                )
                conn.execute(
                    "UPDATE reservations SET status = 'released' "
                    "WHERE reservation_id = ?",
                    (reservation_id,),
                )
                record["status"] = "released"
            return record

    def report(self):
        with self._transaction() as conn:
            items = [
                {"sku": sku, "available": int(available)}
                for sku, available in conn.execute(
                    "SELECT sku, available FROM items ORDER BY sku"
                )
            ]
            active_quantities = [
                int(quantity)
                for (quantity,) in conn.execute(
                    "SELECT quantity FROM reservations WHERE status = 'active'"
                )
            ]
        return {
            "items": items,
            "active_reservations": len(active_quantities),
            "reserved_units": sum(active_quantities),
        }
