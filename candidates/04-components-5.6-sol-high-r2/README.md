# Inventory reservation service

This is a Python standard-library inventory service backed by SQLite. Each
operation opens and closes its own database connection, and data remains in the
database file between CLI invocations or `Store` instances.

## Command line

Run commands from the project directory:

```text
python -m reservation --db inventory.db add --sku widget --quantity 10
python -m reservation --db inventory.db reserve --key order-123 --sku widget --quantity 2
python -m reservation --db inventory.db release --id 1
python -m reservation --db inventory.db report
```

Start the HTTP API with:

```text
python -m reservation --db inventory.db serve --host 127.0.0.1 --port 8080
```

The API exposes `GET /health`, `GET /report`, item creation and lookup under
`/items`, and reservation creation and release under `/reservations` as
described in [TASK.md](TASK.md).

## Tests

```text
python -m unittest discover -s tests -v
```
