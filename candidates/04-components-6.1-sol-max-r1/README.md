# Inventory reservation service

Python's standard library and SQLite are the only dependencies. Run commands
from this directory, using Python 3. No installation is required.

```sh
python -m reservation --db stock.db add --sku widget --quantity 10
python -m reservation --db stock.db reserve --key order-123 --sku widget --quantity 3
python -m reservation --db stock.db release --id 1
python -m reservation --db stock.db report
python -m reservation --db stock.db serve --host 127.0.0.1 --port 8000
```

Finite commands print one JSON record to stdout. Errors print a JSON object
with an `error` string to stderr and exit with status 2. Stop the server with
Ctrl+C. The default server address is `127.0.0.1:8000`.

The HTTP API accepts JSON objects and returns JSON on every route:

| Method | Path | Request body | Success |
| --- | --- | --- | --- |
| GET | `/health` | | 200 |
| POST | `/items` | `{"sku":"widget","quantity":10}` | 201 |
| GET | `/items/<URL-encoded-sku>` | | 200 |
| POST | `/reservations` | `{"idempotency_key":"order-123","sku":"widget","quantity":3}` | 201 |
| POST | `/reservations/1/release` | `{}` | 200 |
| GET | `/report` | | 200 |

Missing records and unknown routes return 404; stock or idempotency conflicts
return 409; invalid requests return 400. Bodies are limited to 64 KiB (413
when exceeded). URL-encode SKU path segments, including any slash in a SKU.

Direct use shares the same file and state:

```python
from reservation import Store

store = Store("stock.db")
store.add_item("widget", 10)
reservation = store.reserve("order-456", "widget", 2)
store.release(reservation["reservation_id"])
print(store.report())
```

Use a persistent database file, rather than SQLite's `:memory:` database.
Each operation closes its connection, so no store close method is needed.
Writes use SQLite transactions to coordinate concurrent Store instances and
HTTP requests. SKU and key whitespace is stripped. Quantities and reservation
IDs must be positive integers; booleans are rejected.

Adding an existing SKU adds units. Retrying an identical reservation returns
the same reservation ID and its current status, including `released`, without
changing stock. Changing the SKU or quantity for an existing key fails.
Release restores units only once. Reports sort items by SKU and count only
active reservations.

Run all tests, including local loopback HTTP tests:

```sh
python -m unittest discover -s tests -v
```
