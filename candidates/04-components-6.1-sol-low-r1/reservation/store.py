import sqlite3
from contextlib import contextmanager


class Conflict(Exception):
    pass


class NotFound(Exception):
    pass


class Store:
    def __init__(self, db_path):
        self.db_path = db_path
        with self._transaction(write=True) as db:
            db.execute("CREATE TABLE IF NOT EXISTS items (sku TEXT PRIMARY KEY, available INTEGER NOT NULL CHECK(available >= 0))")
            db.execute("""CREATE TABLE IF NOT EXISTS reservations (
                reservation_id INTEGER PRIMARY KEY AUTOINCREMENT,
                idempotency_key TEXT NOT NULL UNIQUE,
                sku TEXT NOT NULL REFERENCES items(sku),
                quantity INTEGER NOT NULL CHECK(quantity > 0),
                status TEXT NOT NULL CHECK(status IN ('active', 'released'))
            )""")

    @contextmanager
    def _transaction(self, write=False):
        db = sqlite3.connect(self.db_path, timeout=30)
        db.row_factory = sqlite3.Row
        try:
            db.execute("PRAGMA foreign_keys = ON")
            db.execute("BEGIN IMMEDIATE" if write else "BEGIN")
            yield db
            db.commit()
        except BaseException:
            db.rollback()
            raise
        finally:
            db.close()

    @staticmethod
    def _text(value, name):
        if not isinstance(value, str) or not value.strip():
            raise ValueError(f"{name} must be a nonblank string")
        return value.strip()

    @staticmethod
    def _positive(value, name):
        if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
            raise ValueError(f"{name} must be a positive integer")
        return value

    def add_item(self, sku, quantity):
        sku = self._text(sku, "sku")
        quantity = self._positive(quantity, "quantity")
        with self._transaction(write=True) as db:
            db.execute("INSERT INTO items VALUES (?, ?) ON CONFLICT(sku) DO UPDATE SET available = available + excluded.available", (sku, quantity))
            return dict(db.execute("SELECT * FROM items WHERE sku = ?", (sku,)).fetchone())

    def get_item(self, sku):
        sku = self._text(sku, "sku")
        with self._transaction() as db:
            row = db.execute("SELECT * FROM items WHERE sku = ?", (sku,)).fetchone()
            if row is None:
                raise NotFound("Item not found")
            return dict(row)

    def reserve(self, key, sku, quantity):
        key = self._text(key, "idempotency_key")
        sku = self._text(sku, "sku")
        quantity = self._positive(quantity, "quantity")
        with self._transaction(write=True) as db:
            row = db.execute("SELECT * FROM reservations WHERE idempotency_key = ?", (key,)).fetchone()
            if row is not None:
                if row["sku"] != sku or row["quantity"] != quantity:
                    raise Conflict("Idempotency key already used with different parameters")
                return dict(row)
            item = db.execute("SELECT available FROM items WHERE sku = ?", (sku,)).fetchone()
            if item is None:
                raise NotFound("Item not found")
            if item["available"] < quantity:
                raise Conflict("Insufficient stock")
            db.execute("UPDATE items SET available = available - ? WHERE sku = ?", (quantity, sku))
            cursor = db.execute("INSERT INTO reservations (idempotency_key, sku, quantity, status) VALUES (?, ?, ?, 'active')", (key, sku, quantity))
            return dict(db.execute("SELECT * FROM reservations WHERE reservation_id = ?", (cursor.lastrowid,)).fetchone())

    def release(self, reservation_id):
        reservation_id = self._positive(reservation_id, "reservation_id")
        with self._transaction(write=True) as db:
            row = db.execute("SELECT * FROM reservations WHERE reservation_id = ?", (reservation_id,)).fetchone()
            if row is None:
                raise NotFound("Reservation not found")
            result = dict(row)
            if result["status"] == "active":
                db.execute("UPDATE items SET available = available + ? WHERE sku = ?", (result["quantity"], result["sku"]))
                db.execute("UPDATE reservations SET status = 'released' WHERE reservation_id = ?", (reservation_id,))
                result["status"] = "released"
            return result

    def report(self):
        with self._transaction() as db:
            items = [dict(row) for row in db.execute("SELECT * FROM items ORDER BY sku")]
            count, units = db.execute("SELECT COUNT(*), COALESCE(SUM(quantity), 0) FROM reservations WHERE status = 'active'").fetchone()
            return {"items": items, "active_reservations": count, "reserved_units": units}
