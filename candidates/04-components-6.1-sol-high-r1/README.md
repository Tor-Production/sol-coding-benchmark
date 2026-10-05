# Inventory reservation service

Python standard library only; no installation or dependencies are needed.
Run commands from this directory. The database is created automatically, and
inventory and reservations persist between processes.

```console
python -m reservation --db stock.db add --sku widget --quantity 10
python -m reservation --db stock.db reserve --key order-123 --sku widget --quantity 3
python -m reservation --db stock.db release --id 1
python -m reservation --db stock.db report
python -m reservation --db stock.db serve --host 127.0.0.1 --port 8000
```

Finite commands print one JSON record. Validation, missing records, and stock or
idempotency conflicts print a JSON error to stderr and exit with code 2.
Stop the HTTP service with Ctrl+C.

The HTTP service accepts JSON objects and returns JSON. Its routes are:

| Method | Path | JSON body |
| --- | --- | --- |
| GET | `/health` | |
| POST | `/items` | `{"sku": "widget", "quantity": 10}` |
| GET | `/items/<URL-encoded-sku>` | |
| POST | `/reservations` | `{"idempotency_key": "order-123", "sku": "widget", "quantity": 3}` |
| POST | `/reservations/1/release` | `{}` |
| GET | `/report` | |

Request bodies are limited to 64 KiB. Missing records return 404, conflicts 409,
invalid requests 400, and oversized bodies 413. Adding stock and reserving return
201; other successful routes return 200.

Direct Python usage shares the same database:

```python
from reservation.store import Store, Conflict, NotFound

store = Store("stock.db")
store.add_item("widget", 10)
reservation = store.reserve("order-456", "widget", 2)
store.release(reservation["reservation_id"])
print(store.report())
```

Keys and SKUs are stripped of surrounding whitespace. Reusing a key with the
same parameters returns its existing reservation, including its released status;
different parameters conflict. Release restores units only once. Each operation
closes its SQLite connection, and transactions protect concurrent updates.

Tests: `python -m unittest discover -s tests -v`.
