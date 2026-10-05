# Inventory reservation service

Python 3.9+ and SQLite from the Python standard library are sufficient. Run
commands from this directory; the database file is created on first use and
is shared by the CLI, HTTP API, and direct Python calls.

```sh
python -m reservation --db stock.db add --sku widget --quantity 10
python -m reservation --db stock.db reserve --key order-1 --sku widget --quantity 3
python -m reservation --db stock.db release --id 1
python -m reservation --db stock.db report
python -m reservation --db stock.db serve --host 127.0.0.1 --port 8000
```

Finite commands print one JSON record and exit 0. Validation, stock conflicts,
and missing records print a JSON error to stderr and exit 2. Stop the server
with Ctrl+C.

The HTTP server accepts JSON objects and returns JSON for all routes:

| Method | Path | Body | Success |
| --- | --- | --- | --- |
| GET | `/health` | | 200 |
| POST | `/items` | `{"sku":"widget","quantity":10}` | 201 |
| GET | `/items/widget` | | 200 |
| POST | `/reservations` | `{"idempotency_key":"order-1","sku":"widget","quantity":3}` | 201 |
| POST | `/reservations/1/release` | `{}` | 200 |
| GET | `/report` | | 200 |

URL-encode SKUs in item paths. Invalid requests return 400, missing records or
routes return 404, stock/key conflicts return 409, and bodies over 64 KiB return
413. Errors have the shape `{"error":"message"}`.

```python
from reservation import Store
from reservation.http_api import create_server

store = Store("stock.db")
store.add_item("widget", 10)
reservation = store.reserve("order-2", "widget", 2)
store.release(reservation["reservation_id"])
print(store.report())
```

SKU and key strings are stripped. Adding stock accumulates units; each release
restores units once. Identical reservation retries return the original response
(including its original `active` status after release), while different
parameters for an existing key conflict. The stored status remains `released`,
and reports count only active reservations. Transactions serialize writers even
across separate Store instances, and every call closes its SQLite connection.

For an embedded server, `create_server("stock.db", port=0)` binds an available
port. Start `serve_forever()` in a thread, then call `shutdown()` and
`server_close()` when finished.

Run all supplied and additional tests:

```sh
python -m unittest discover -s tests -v
```
