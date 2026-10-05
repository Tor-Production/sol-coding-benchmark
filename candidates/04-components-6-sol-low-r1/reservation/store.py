"""SQLite inventory store."""
import sqlite3
from contextlib import contextmanager

class Conflict(Exception):
    pass

class NotFound(Exception):
    pass

def _name(value, label):
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{label} must be a nonblank string")
    return value.strip()

def _positive(value, label):
    if type(value) is not int or value <= 0:
        raise ValueError(f"{label} must be a positive integer")
    return value

def _record(row):
    return dict(zip(("reservation_id", "idempotency_key", "sku", "quantity", "status"), row))

class Store:
    def __init__(self, db_path):
        self.db_path = db_path
        with self._db() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS items (
                    sku TEXT PRIMARY KEY, available INTEGER NOT NULL CHECK(available >= 0));
                CREATE TABLE IF NOT EXISTS reservations (
                    reservation_id INTEGER PRIMARY KEY,
                    idempotency_key TEXT NOT NULL UNIQUE,
                    sku TEXT NOT NULL REFERENCES items(sku),
                    quantity INTEGER NOT NULL CHECK(quantity > 0),
                    status TEXT NOT NULL CHECK(status IN ('active', 'released')));
            """)

    @contextmanager
    def _db(self, write=False):
        db = sqlite3.connect(self.db_path, timeout=30)
        try:
            if write:
                db.execute("BEGIN IMMEDIATE")
            else:
                db.execute("BEGIN")
            yield db
            db.commit()
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()

    def add_item(self, sku, quantity):
        sku = _name(sku, "sku")
        quantity = _positive(quantity, "quantity")
        with self._db(True) as db:
            db.execute("""INSERT INTO items VALUES (?, ?)
                ON CONFLICT(sku) DO UPDATE SET available=available+excluded.available""", (sku, quantity))
            available = db.execute("SELECT available FROM items WHERE sku=?", (sku,)).fetchone()[0]
            return {"sku": sku, "available": available}

    def get_item(self, sku):
        sku = _name(sku, "sku")
        with self._db() as db:
            row = db.execute("SELECT available FROM items WHERE sku=?", (sku,)).fetchone()
        if row is None:
            raise NotFound(f"SKU {sku!r} not found")
        return {"sku": sku, "available": row[0]}

    def reserve(self, key, sku, quantity):
        key = _name(key, "idempotency_key")
        sku = _name(sku, "sku")
        quantity = _positive(quantity, "quantity")
        with self._db(True) as db:
            row = db.execute("SELECT * FROM reservations WHERE idempotency_key=?", (key,)).fetchone()
            if row:
                if row[2:4] != (sku, quantity):
                    raise Conflict("idempotency key already used with different parameters")
                return _record(row)
            item = db.execute("SELECT available FROM items WHERE sku=?", (sku,)).fetchone()
            if item is None:
                raise NotFound(f"SKU {sku!r} not found")
            if item[0] < quantity:
                raise Conflict("insufficient stock")
            db.execute("UPDATE items SET available=available-? WHERE sku=?", (quantity, sku))
            cursor = db.execute("INSERT INTO reservations(idempotency_key,sku,quantity,status) VALUES (?,?,?,'active')", (key, sku, quantity))
            return _record((cursor.lastrowid, key, sku, quantity, "active"))

    def release(self, reservation_id):
        reservation_id = _positive(reservation_id, "reservation_id")
        with self._db(True) as db:
            row = db.execute("SELECT * FROM reservations WHERE reservation_id=?", (reservation_id,)).fetchone()
            if row is None:
                raise NotFound(f"reservation {reservation_id} not found")
            if row[4] == "active":
                db.execute("UPDATE items SET available=available+? WHERE sku=?", (row[3], row[2]))
                db.execute("UPDATE reservations SET status='released' WHERE reservation_id=?", (reservation_id,))
            return _record((*row[:4], "released"))

    def report(self):
        with self._db() as db:
            items = [{"sku": sku, "available": available} for sku, available in db.execute("SELECT sku,available FROM items ORDER BY sku")]
            count, units = db.execute("SELECT COUNT(*), COALESCE(SUM(quantity),0) FROM reservations WHERE status='active'").fetchone()
        return {"items": items, "active_reservations": count, "reserved_units": units}
