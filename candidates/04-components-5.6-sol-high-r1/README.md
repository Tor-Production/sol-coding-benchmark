# Inventory reservation service

A small Python standard-library service backed by SQLite. Inventory, active
reservations, releases, and idempotency keys persist in the database file and
are shared by the direct API, CLI, and HTTP server.

## Command line

Run commands from the repository root:

```console
python -m reservation --db inventory.db add --sku widget --quantity 10
python -m reservation --db inventory.db reserve --key order-123 --sku widget --quantity 2
python -m reservation --db inventory.db release --id 1
python -m reservation --db inventory.db report
```

Each finite command prints one JSON object. Validation errors, missing records,
and conflicts print a JSON error to stderr and exit with status 2.

## HTTP server

```console
python -m reservation --db inventory.db serve --host 127.0.0.1 --port 8000
```

The server exposes `GET /health`, `POST /items`, `GET /items/<sku>`,
`POST /reservations`, `POST /reservations/<id>/release`, and `GET /report`.
Request bodies are JSON objects. Stop the server with Ctrl+C.

## Python API

```python
from reservation.store import Store

store = Store("inventory.db")
store.add_item("widget", 10)
reservation = store.reserve("order-123", "widget", 2)
store.release(reservation["reservation_id"])
print(store.report())
```

## Tests

Tests: `python -m unittest discover -s tests -v`.
