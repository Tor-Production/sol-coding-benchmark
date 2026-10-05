"""SQLite persistence for the inventory reservation service."""
import sqlite3
from contextlib import contextmanager

class Conflict(Exception): pass
class NotFound(Exception): pass

def _text(value, name):
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} must be a nonblank string")
    return value.strip()

def _positive_int(value, name):
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise ValueError(f"{name} must be a positive integer")
    return value

class Store:
    def __init__(self, db_path):
        self.db_path = str(db_path)
        with self._connect() as connection:
            connection.executescript("""
                CREATE TABLE IF NOT EXISTS items (
                    sku TEXT PRIMARY KEY,
                    available INTEGER NOT NULL CHECK (available >= 0));
                CREATE TABLE IF NOT EXISTS reservations (
                    reservation_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    idempotency_key TEXT NOT NULL UNIQUE,
                    sku TEXT NOT NULL,
                    quantity INTEGER NOT NULL CHECK (quantity > 0),
                    status TEXT NOT NULL CHECK (status IN ('active','released')),
                    FOREIGN KEY (sku) REFERENCES items(sku));""")

    @contextmanager
    def _connect(self):
        connection = sqlite3.connect(self.db_path, timeout=30)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        try:
            with connection:
                yield connection
        finally:
            connection.close()

    @staticmethod
    def _reservation(row):
        return {name: row[name] for name in
                ("reservation_id", "idempotency_key", "sku", "quantity", "status")}

    def add_item(self, sku, quantity):
        sku, quantity = _text(sku, "sku"), _positive_int(quantity, "quantity")
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute("""INSERT INTO items VALUES (?, ?)
                ON CONFLICT(sku) DO UPDATE SET available=available+excluded.available""",
                               (sku, quantity))
            available = connection.execute(
                "SELECT available FROM items WHERE sku=?", (sku,)).fetchone()[0]
        return {"sku": sku, "available": available}

    def get_item(self, sku):
        sku = _text(sku, "sku")
        with self._connect() as connection:
            row = connection.execute("SELECT * FROM items WHERE sku=?", (sku,)).fetchone()
        if row is None:
            raise NotFound(f"item not found: {sku}")
        return {"sku": row["sku"], "available": row["available"]}

    def reserve(self, key, sku, quantity):
        key = _text(key, "idempotency_key")
        sku = _text(sku, "sku")
        quantity = _positive_int(quantity, "quantity")
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT * FROM reservations WHERE idempotency_key=?", (key,)).fetchone()
            if row is not None:
                if row["sku"] != sku or row["quantity"] != quantity:
                    raise Conflict("idempotency key used with different parameters")
                return self._reservation(row)
            item = connection.execute(
                "SELECT available FROM items WHERE sku=?", (sku,)).fetchone()
            if item is None:
                raise NotFound(f"item not found: {sku}")
            if item[0] < quantity:
                raise Conflict("insufficient stock")
            connection.execute("UPDATE items SET available=available-? WHERE sku=?",
                               (quantity, sku))
            cursor = connection.execute("""INSERT INTO reservations
                (idempotency_key,sku,quantity,status) VALUES (?,?,?,'active')""",
                                        (key, sku, quantity))
            row = connection.execute("SELECT * FROM reservations WHERE reservation_id=?",
                                     (cursor.lastrowid,)).fetchone()
        return self._reservation(row)

    def release(self, reservation_id):
        reservation_id = _positive_int(reservation_id, "reservation_id")
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute("SELECT * FROM reservations WHERE reservation_id=?",
                                     (reservation_id,)).fetchone()
            if row is None:
                raise NotFound(f"reservation not found: {reservation_id}")
            if row["status"] == "active":
                connection.execute("UPDATE items SET available=available+? WHERE sku=?",
                                   (row["quantity"], row["sku"]))
                connection.execute("UPDATE reservations SET status='released' WHERE reservation_id=?",
                                   (reservation_id,))
                row = connection.execute("SELECT * FROM reservations WHERE reservation_id=?",
                                         (reservation_id,)).fetchone()
        return self._reservation(row)

    def report(self):
        with self._connect() as connection:
            connection.execute("BEGIN")
            rows = connection.execute("SELECT * FROM items ORDER BY sku").fetchall()
            totals = connection.execute("""SELECT COUNT(*), COALESCE(SUM(quantity),0)
                FROM reservations WHERE status='active'""").fetchone()
        return {"items": [{"sku": r["sku"], "available": r["available"]} for r in rows],
                "active_reservations": totals[0], "reserved_units": totals[1]}
