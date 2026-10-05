# Inventory reservation service

A persistent SQLite inventory and reservation service implemented with only
the Python standard library.

## Command line

All commands take the database path before the subcommand:

```console
python -m reservation --db inventory.db add --sku widget --quantity 10
python -m reservation --db inventory.db reserve --key order-123 --sku widget --quantity 2
python -m reservation --db inventory.db release --id 1
python -m reservation --db inventory.db report
```

Successful finite commands print one JSON object. Validation, missing-record,
stock, and idempotency conflicts print a JSON error to stderr and exit with
status 2.

## HTTP server

Start the service on a chosen host and port:

```console
python -m reservation --db inventory.db serve --host 127.0.0.1 --port 8000
```

The server provides `GET /health`, `POST /items`, `GET /items/<sku>`,
`POST /reservations`, `POST /reservations/<id>/release`, and `GET /report`.
POST bodies are JSON objects and are limited to 64 KiB.

## Tests

```console
python -m unittest discover -s tests -v
```
