"""Persistent inventory with transactionally serialized stock changes."""

from contextlib import contextmanager
import sqlite3


class Conflict(Exception):
    pass


class NotFound(Exception):
    pass


class Store:
    def __init__(self, db_path):
        self.db_path = str(db_path)
        with self._transaction(write=True) as db:
            # Decimal text preserves Python integers beyond SQLite's 64-bit
            # range. All arithmetic happens under the same write transaction.
            db.execute("""CREATE TABLE IF NOT EXISTS items (
                sku TEXT PRIMARY KEY, available TEXT NOT NULL
                CHECK (available >= 0))""")
            db.execute("""CREATE TABLE IF NOT EXISTS reservations (
                reservation_id INTEGER PRIMARY KEY AUTOINCREMENT,
                idempotency_key TEXT NOT NULL UNIQUE,
                sku TEXT NOT NULL REFERENCES items(sku),
                quantity TEXT NOT NULL CHECK (quantity > 0),
                status TEXT NOT NULL CHECK (status IN ('active', 'released')))""")

    @contextmanager
    def _transaction(self, write=False):
        db = sqlite3.connect(self.db_path, timeout=30, isolation_level=None)
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

    @staticmethod
    def _item(row):
        return {"sku": row["sku"], "available": int(row["available"])}

    @staticmethod
    def _reservation(row):
        record = dict(row)
        record["quantity"] = int(record["quantity"])
        return record

    def add_item(self, sku, quantity):
        sku = self._text(sku, "sku")
        quantity = self._positive(quantity, "quantity")
        with self._transaction(write=True) as db:
            row = db.execute("SELECT available FROM items WHERE sku = ?", (sku,)).fetchone()
            available = quantity + (int(row["available"]) if row else 0)
            db.execute("""INSERT INTO items (sku, available) VALUES (?, ?)
                ON CONFLICT(sku) DO UPDATE SET available = excluded.available""",
                       (sku, str(available)))
            return {"sku": sku, "available": available}

    def get_item(self, sku):
        sku = self._text(sku, "sku")
        with self._transaction() as db:
            row = db.execute("SELECT sku, available FROM items WHERE sku = ?", (sku,)).fetchone()
            if row is None:
                raise NotFound(f"Item {sku!r} not found")
            return self._item(row)

    def reserve(self, key, sku, quantity):
        key = self._text(key, "idempotency_key")
        sku = self._text(sku, "sku")
        quantity = self._positive(quantity, "quantity")
        with self._transaction(write=True) as db:
            existing = db.execute("SELECT * FROM reservations WHERE idempotency_key = ?",
                                  (key,)).fetchone()
            if existing is not None:
                if existing["sku"] != sku or int(existing["quantity"]) != quantity:
                    raise Conflict("Idempotency key already used with different parameters")
                return self._reservation(existing)
            item = db.execute("SELECT available FROM items WHERE sku = ?", (sku,)).fetchone()
            if item is None:
                raise NotFound(f"Item {sku!r} not found")
            available = int(item["available"])
            if available < quantity:
                raise Conflict("Insufficient stock")
            db.execute("UPDATE items SET available = ? WHERE sku = ?", (str(available - quantity), sku))
            cursor = db.execute("""INSERT INTO reservations
                (idempotency_key, sku, quantity, status) VALUES (?, ?, ?, 'active')""",
                                (key, sku, str(quantity)))
            return self._reservation(db.execute("SELECT * FROM reservations WHERE reservation_id = ?",
                                   (cursor.lastrowid,)).fetchone())

    def release(self, reservation_id):
        reservation_id = self._positive(reservation_id, "reservation_id")
        with self._transaction(write=True) as db:
            row = db.execute("SELECT * FROM reservations WHERE reservation_id = ?",
                             (str(reservation_id),)).fetchone()
            if row is None:
                raise NotFound(f"Reservation {reservation_id} not found")
            record = self._reservation(row)
            if record["status"] == "active":
                item = db.execute("SELECT available FROM items WHERE sku = ?",
                                  (record["sku"],)).fetchone()
                db.execute("UPDATE items SET available = ? WHERE sku = ?",
                           (str(int(item["available"]) + record["quantity"]), record["sku"]))
                db.execute("UPDATE reservations SET status = 'released' WHERE reservation_id = ?",
                           (reservation_id,))
                record["status"] = "released"
            return record

    def report(self):
        with self._transaction() as db:
            items = [self._item(row) for row in db.execute("SELECT sku, available FROM items ORDER BY sku")]
            quantities = [int(row[0]) for row in db.execute(
                "SELECT quantity FROM reservations WHERE status = 'active'")]
            return {"items": items, "active_reservations": len(quantities), "reserved_units": sum(quantities)}
