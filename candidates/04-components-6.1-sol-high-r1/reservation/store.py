"""Transactional inventory storage, with one connection per operation."""

from contextlib import contextmanager
import sqlite3


class Conflict(Exception):
    pass


class NotFound(Exception):
    pass


class Store:
    def __init__(self, db_path):
        self.db_path = db_path
        with self._transaction(write=True) as connection:
            connection.execute("""
                CREATE TABLE IF NOT EXISTS items (
                    sku TEXT PRIMARY KEY,
                    available TEXT NOT NULL CHECK (
                        available <> '' AND available NOT GLOB '*[^0-9]*'
                    )
                )
            """)
            connection.execute("""
                CREATE TABLE IF NOT EXISTS reservations (
                    reservation_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    idempotency_key TEXT NOT NULL UNIQUE,
                    sku TEXT NOT NULL REFERENCES items(sku),
                    quantity TEXT NOT NULL CHECK (
                        quantity <> '' AND quantity <> '0'
                        AND quantity NOT GLOB '*[^0-9]*'
                    ),
                    status TEXT NOT NULL CHECK (status IN ('active', 'released'))
                )
            """)

    @contextmanager
    def _transaction(self, *, write=False):
        connection = sqlite3.connect(self.db_path, timeout=30)
        connection.row_factory = sqlite3.Row
        try:
            connection.execute("PRAGMA foreign_keys = ON")
            connection.execute("BEGIN IMMEDIATE" if write else "BEGIN")
            yield connection
            connection.commit()
        except BaseException:
            connection.rollback()
            raise
        finally:
            connection.close()

    @staticmethod
    def _text(value, name):
        if not isinstance(value, str) or not value.strip():
            raise ValueError(f"{name} must be a nonblank string")
        return value.strip()

    @staticmethod
    def _positive_integer(value, name):
        if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
            raise ValueError(f"{name} must be a positive integer")
        return value

    @staticmethod
    def _item(connection, sku):
        row = connection.execute(
            "SELECT sku, available FROM items WHERE sku = ?", (sku,)
        ).fetchone()
        if row is None:
            raise NotFound(f"Item not found: {sku}")
        return {"sku": row["sku"], "available": int(row["available"])}

    @staticmethod
    def _reservation(row):
        record = dict(row)
        record["quantity"] = int(record["quantity"])
        return record

    def add_item(self, sku, quantity):
        sku = self._text(sku, "sku")
        quantity = self._positive_integer(quantity, "quantity")
        with self._transaction(write=True) as connection:
            row = connection.execute(
                "SELECT available FROM items WHERE sku = ?", (sku,)
            ).fetchone()
            available = quantity + (int(row[0]) if row is not None else 0)
            # Decimal text preserves Python integers beyond SQLite's signed
            # 64-bit range. Arithmetic is protected by BEGIN IMMEDIATE.
            connection.execute("""
                INSERT INTO items (sku, available) VALUES (?, ?)
                ON CONFLICT(sku) DO UPDATE SET available = excluded.available
            """, (sku, str(available)))
            return self._item(connection, sku)

    def get_item(self, sku):
        sku = self._text(sku, "sku")
        with self._transaction() as connection:
            return self._item(connection, sku)

    def reserve(self, key, sku, quantity):
        key = self._text(key, "idempotency_key")
        sku = self._text(sku, "sku")
        quantity = self._positive_integer(quantity, "quantity")
        with self._transaction(write=True) as connection:
            existing = connection.execute(
                "SELECT * FROM reservations WHERE idempotency_key = ?", (key,)
            ).fetchone()
            if existing is not None:
                if existing["sku"] != sku or int(existing["quantity"]) != quantity:
                    raise Conflict("Idempotency key already used with different parameters")
                return self._reservation(existing)
            item = self._item(connection, sku)
            if item["available"] < quantity:
                raise Conflict(f"Insufficient stock for: {sku}")
            connection.execute(
                "UPDATE items SET available = ? WHERE sku = ?",
                (str(item["available"] - quantity), sku),
            )
            cursor = connection.execute("""
                INSERT INTO reservations (idempotency_key, sku, quantity, status)
                VALUES (?, ?, ?, 'active')
            """, (key, sku, str(quantity)))
            return self._reservation(connection.execute(
                "SELECT * FROM reservations WHERE reservation_id = ?",
                (cursor.lastrowid,),
            ).fetchone())

    def release(self, reservation_id):
        reservation_id = self._positive_integer(reservation_id, "reservation_id")
        with self._transaction(write=True) as connection:
            row = connection.execute(
                "SELECT * FROM reservations WHERE reservation_id = ?", (str(reservation_id),)
            ).fetchone()
            if row is None:
                raise NotFound(f"Reservation not found: {reservation_id}")
            record = self._reservation(row)
            if record["status"] == "active":
                available = self._item(connection, record["sku"])["available"]
                connection.execute(
                    "UPDATE items SET available = ? WHERE sku = ?",
                    (str(available + record["quantity"]), record["sku"]),
                )
                connection.execute(
                    "UPDATE reservations SET status = 'released' WHERE reservation_id = ?",
                    (reservation_id,),
                )
                record["status"] = "released"
            return record

    def report(self):
        with self._transaction() as connection:
            items = [{"sku": row["sku"], "available": int(row["available"])}
                     for row in connection.execute(
                "SELECT sku, available FROM items ORDER BY sku"
            )]
            count = units = 0
            for row in connection.execute(
                "SELECT quantity FROM reservations WHERE status = 'active'"
            ):
                count += 1
                units += int(row[0])
            return {"items": items, "active_reservations": count,
                    "reserved_units": units}
