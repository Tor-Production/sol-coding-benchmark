# Inventory reservation service

A persistent inventory and idempotent reservation service implemented with
Python's standard library and SQLite.

## Run the CLI

All commands take the database path before the subcommand:

```console
python -m reservation --db inventory.db add --sku widget --quantity 10
python -m reservation --db inventory.db reserve --key order-123 --sku widget --quantity 2
python -m reservation --db inventory.db release --id 1
python -m reservation --db inventory.db report
```

Successful commands print one JSON object. Domain and validation errors print
a JSON error to stderr and exit with status 2.

## Run the HTTP server

```console
python -m reservation --db inventory.db serve --host 127.0.0.1 --port 8000
```

The JSON endpoints are `GET /health`, `POST /items`, `GET /items/<sku>`,
`POST /reservations`, `POST /reservations/<id>/release`, and `GET /report`.
Stop the server with Ctrl+C.

## Test

```console
python -m unittest discover -s tests -v
```
