"""SQLite inventory store."""
import sqlite3
from contextlib import contextmanager

class Conflict(Exception):
    pass

class NotFound(Exception):
    pass

def name(value, label):
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{label} must be a nonblank string")
    return value.strip()

def positive(value, label):
    if type(value) is not int or value <= 0:
        raise ValueError(f"{label} must be a positive integer")
    return value

def record(row):
    return dict(zip(("reservation_id", "idempotency_key", "sku", "quantity", "status"), row))

class Store:
    def __init__(self, db_path):
        self.db_path = db_path
        with self.transaction() as db:
            db.execute("CREATE TABLE IF NOT EXISTS items (sku TEXT PRIMARY KEY, available INTEGER NOT NULL CHECK(available >= 0))")
            db.execute("""CREATE TABLE IF NOT EXISTS reservations (
                reservation_id INTEGER PRIMARY KEY AUTOINCREMENT,
                idempotency_key TEXT NOT NULL UNIQUE,
                sku TEXT NOT NULL,
                quantity INTEGER NOT NULL CHECK(quantity > 0),
                status TEXT NOT NULL CHECK(status IN ('active', 'released')))""")

    @contextmanager
    def transaction(self, write=True):
        db = sqlite3.connect(self.db_path, timeout=30)
        try:
            db.execute("BEGIN IMMEDIATE" if write else "BEGIN")
            yield db
            db.commit()
        except BaseException:
            db.rollback()
            raise
        finally:
            db.close()

    def add_item(self, sku, quantity):
        sku, quantity = name(sku, "sku"), positive(quantity, "quantity")
        with self.transaction() as db:
            db.execute("INSERT INTO items VALUES (?, ?) ON CONFLICT(sku) DO UPDATE SET available = available + excluded.available", (sku, quantity))
            available = db.execute("SELECT available FROM items WHERE sku=?", (sku,)).fetchone()[0]
            return {"sku": sku, "available": available}

    def get_item(self, sku):
        sku = name(sku, "sku")
        with self.transaction(False) as db:
            row = db.execute("SELECT available FROM items WHERE sku=?", (sku,)).fetchone()
            if row is None:
                raise NotFound(f"item {sku!r} not found")
            return {"sku": sku, "available": row[0]}

    def reserve(self, key, sku, quantity):
        key, sku, quantity = name(key, "idempotency_key"), name(sku, "sku"), positive(quantity, "quantity")
        with self.transaction() as db:
            row = db.execute("SELECT reservation_id,idempotency_key,sku,quantity,status FROM reservations WHERE idempotency_key=?", (key,)).fetchone()
            if row:
                if row[2:4] != (sku, quantity):
                    raise Conflict("idempotency key already used with different parameters")
                return record(row)
            stock = db.execute("SELECT available FROM items WHERE sku=?", (sku,)).fetchone()
            if stock is None:
                raise NotFound(f"item {sku!r} not found")
            if stock[0] < quantity:
                raise Conflict("insufficient stock")
            db.execute("UPDATE items SET available=available-? WHERE sku=?", (quantity, sku))
            cursor = db.execute("INSERT INTO reservations (idempotency_key,sku,quantity,status) VALUES (?,?,?,'active')", (key, sku, quantity))
            return {"reservation_id": cursor.lastrowid, "idempotency_key": key, "sku": sku, "quantity": quantity, "status": "active"}

    def release(self, reservation_id):
        reservation_id = positive(reservation_id, "reservation_id")
        with self.transaction() as db:
            row = db.execute("SELECT reservation_id,idempotency_key,sku,quantity,status FROM reservations WHERE reservation_id=?", (reservation_id,)).fetchone()
            if row is None:
                raise NotFound(f"reservation {reservation_id} not found")
            result = record(row)
            if result["status"] == "active":
                db.execute("UPDATE items SET available=available+? WHERE sku=?", (result["quantity"], result["sku"]))
                db.execute("UPDATE reservations SET status='released' WHERE reservation_id=?", (reservation_id,))
                result["status"] = "released"
            return result

    def report(self):
        with self.transaction(False) as db:
            items = [{"sku": sku, "available": available} for sku, available in db.execute("SELECT sku,available FROM items ORDER BY sku")]
            count, units = db.execute("SELECT COUNT(*),COALESCE(SUM(quantity),0) FROM reservations WHERE status='active'").fetchone()
            return {"items": items, "active_reservations": count, "reserved_units": units}
