# Inventory reservation service

An inventory reservation service backed by SQLite. It uses only Python's
standard library. The database file is created on first use and can be shared
by the CLI, HTTP server, and direct `Store` calls.

## CLI

From this directory, run:

```sh
python -m reservation --db stock.db add --sku widget --quantity 10
python -m reservation --db stock.db reserve --key order-123 --sku widget --quantity 2
python -m reservation --db stock.db release --id 1
python -m reservation --db stock.db report
```

Successful finite commands print one JSON object. Invalid input, missing
records, and conflicts print a JSON error to stderr and exit with status 2.

## HTTP server

```sh
python -m reservation --db stock.db serve --host 127.0.0.1 --port 8000
```

Send JSON request bodies to `POST /items`, `POST /reservations`, and
`POST /reservations/<id>/release`. Read `GET /health`, `GET /items/<sku>`, and
`GET /report`. Reserve requests use an idempotency key: retrying the same key
and parameters returns the original reservation response without changing
stock, even after release.

## Python API

```python
from reservation.store import Store

store = Store("stock.db")
store.add_item("widget", 10)
reservation = store.reserve("order-123", "widget", 2)
store.release(reservation["reservation_id"])
print(store.report())
```

Run the tests with `python -m unittest discover -s tests -v`.
