import sqlite3
from contextlib import contextmanager


class Conflict(Exception):
    pass


class NotFound(Exception):
    pass


def text(value):
    if not isinstance(value, str) or not value.strip():
        raise ValueError("Expected a nonblank string")
    return value.strip()


def positive(value):
    if type(value) is not int or value <= 0:
        raise ValueError("Expected a positive integer")
    return value


class Store:
    def __init__(self, db_path):
        self.db_path = str(db_path)
        with self._connection() as c:
            c.executescript("""
                CREATE TABLE IF NOT EXISTS items(sku TEXT PRIMARY KEY, available INTEGER NOT NULL);
                CREATE TABLE IF NOT EXISTS reservations(
                    reservation_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    idempotency_key TEXT UNIQUE NOT NULL, sku TEXT NOT NULL,
                    quantity INTEGER NOT NULL, status TEXT NOT NULL);
            """)

    @contextmanager
    def _connection(self):
        c = sqlite3.connect(self.db_path, timeout=15)
        c.row_factory = sqlite3.Row
        try:
            with c:
                yield c
        finally:
            c.close()

    def add_item(self, sku, quantity):
        sku, quantity = text(sku), positive(quantity)
        with self._connection() as c:
            c.execute("BEGIN IMMEDIATE")
            c.execute("INSERT INTO items VALUES (?, ?) ON CONFLICT(sku) DO UPDATE SET available=available+excluded.available", (sku, quantity))
            return dict(c.execute("SELECT * FROM items WHERE sku=?", (sku,)).fetchone())

    def get_item(self, sku):
        sku = text(sku)
        with self._connection() as c:
            row = c.execute("SELECT * FROM items WHERE sku=?", (sku,)).fetchone()
            if row is None:
                raise NotFound("SKU not found")
            return dict(row)

    def reserve(self, key, sku, quantity):
        key, sku, quantity = text(key), text(sku), positive(quantity)
        with self._connection() as c:
            c.execute("BEGIN IMMEDIATE")
            row = c.execute("SELECT * FROM reservations WHERE idempotency_key=?", (key,)).fetchone()
            if row:
                if row["sku"] != sku or row["quantity"] != quantity:
                    raise Conflict("Idempotency key reused")
                return dict(row)
            item = c.execute("SELECT available FROM items WHERE sku=?", (sku,)).fetchone()
            if item is None:
                raise NotFound("SKU not found")
            if item[0] < quantity:
                raise Conflict("Insufficient stock")
            c.execute("UPDATE items SET available=available-? WHERE sku=?", (quantity, sku))
            cursor = c.execute("INSERT INTO reservations(idempotency_key,sku,quantity,status) VALUES (?,?,?,'active')", (key, sku, quantity))
            return dict(c.execute("SELECT * FROM reservations WHERE reservation_id=?", (cursor.lastrowid,)).fetchone())

    def release(self, reservation_id):
        reservation_id = positive(reservation_id)
        with self._connection() as c:
            c.execute("BEGIN IMMEDIATE")
            row = c.execute("SELECT * FROM reservations WHERE reservation_id=?", (reservation_id,)).fetchone()
            if row is None:
                raise NotFound("Reservation not found")
            if row["status"] == "active":
                c.execute("UPDATE items SET available=available+? WHERE sku=?", (row["quantity"], row["sku"]))
                c.execute("UPDATE reservations SET status='released' WHERE reservation_id=?", (reservation_id,))
            return dict(c.execute("SELECT * FROM reservations WHERE reservation_id=?", (reservation_id,)).fetchone())

    def report(self):
        with self._connection() as c:
            c.execute("BEGIN")
            items = [dict(row) for row in c.execute("SELECT * FROM items ORDER BY sku")]
            count, units = c.execute("SELECT count(*),coalesce(sum(quantity),0) FROM reservations WHERE status='active'").fetchone()
            return {"items": items, "active_reservations": count, "reserved_units": units}
