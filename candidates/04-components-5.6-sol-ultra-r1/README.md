# Inventory reservation service

A persistent inventory and reservation service implemented with only Python's
standard library and SQLite.

## Command line

Run commands from the project directory. Every command uses the database named
by `--db`; the file is created automatically when needed.

```console
python -m reservation --db inventory.db add --sku widget --quantity 10
python -m reservation --db inventory.db reserve --key order-123 --sku widget --quantity 2
python -m reservation --db inventory.db release --id 1
python -m reservation --db inventory.db report
```

Successful finite commands print one JSON object. Validation errors, missing
records, and conflicts print a JSON error to stderr and exit with status 2.

Start the threaded HTTP service with:

```console
python -m reservation --db inventory.db serve --host 127.0.0.1 --port 8000
```

The HTTP API provides `GET /health`, `POST /items`, `GET /items/<sku>`,
`POST /reservations`, `POST /reservations/<id>/release`, and `GET /report`.
Request bodies are JSON objects; SKU path components should be URL-encoded.

## Python API

```python
from reservation.store import Store

store = Store("inventory.db")
store.add_item("widget", 10)
reservation = store.reserve("order-123", "widget", 2)
store.release(reservation["reservation_id"])
```

Each operation uses and closes its own SQLite connection, so separate `Store`
instances and the CLI and HTTP interfaces can safely share one database.

## Tests

```console
python -m unittest discover -s tests -v
```
