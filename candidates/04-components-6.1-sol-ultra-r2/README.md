# Inventory reservation service

Requires Python 3.9+ and its standard library, including SQLite. Run commands
from this directory; no dependency installation is needed. The database file
is created automatically. Its parent directory must already exist.

```sh
python -m reservation --db stock.db add --sku widget --quantity 10
python -m reservation --db stock.db reserve --key order-123 --sku widget --quantity 3
python -m reservation --db stock.db report
python -m reservation --db stock.db release --id 1
```

Finite commands print one JSON record and exit 0. Invalid arguments, missing
records, and stock/idempotency conflicts print a JSON error to stderr and exit
2. `--help` describes each command.

Start the HTTP service with:

```sh
python -m reservation --db stock.db serve --host 127.0.0.1 --port 8000
```

Stop it with Ctrl+C. HTTP, CLI, and direct Python calls share the database.

| Method | Route | JSON request | Success |
| --- | --- | --- | --- |
| GET | `/health` | | 200, `{"ok": true}` |
| POST | `/items` | `{"sku": "widget", "quantity": 10}` | 201, item |
| GET | `/items/<URL-encoded-sku>` | | 200, item |
| POST | `/reservations` | `{"idempotency_key": "order-123", "sku": "widget", "quantity": 3}` | 201, reservation |
| POST | `/reservations/<id>/release` | `{}` | 200, reservation |
| GET | `/report` | | 200, report |

Send JSON objects for POST requests. Bodies are limited to 64 KiB. Errors use
`{"error": "message"}` with status 400 for invalid input, 404 for missing routes
or records, 409 for conflicts, and 413 for oversized bodies.

Direct Python use:

```python
from reservation import Store

store = Store("stock.db")
store.add_item("widget", 10)
reservation = store.reserve("order-456", "widget", 2)
print(store.report())
store.release(reservation["reservation_id"])
```

SKU and key whitespace is stripped. Quantities and IDs must be positive integers;
booleans are rejected. Adding stock increases the current available quantity.
An identical reservation retry returns the original reservation, including its
current status after release. A different request using that key conflicts.
Repeated release restores stock only once. SQLite transactions serialize
mutations across threads and separate Store instances, and reports read a
consistent snapshot. Each call closes its database connection, so no Store
close method is needed. State persists when the service or Store is reopened.

Run the tests, including local loopback HTTP checks:

```sh
python -m unittest discover -s tests -v
```
