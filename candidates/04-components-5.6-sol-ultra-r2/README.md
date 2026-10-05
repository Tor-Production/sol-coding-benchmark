# Inventory reservation service

This is a standard-library Python service backed by SQLite. Inventory,
reservations, releases, and idempotency records persist in the database file
and are shared by the direct Python API, CLI, and HTTP API.

## Run the CLI

Run commands from the project directory:

```console
python -m reservation --db inventory.db add --sku widget --quantity 10
python -m reservation --db inventory.db reserve --key order-123 --sku widget --quantity 2
python -m reservation --db inventory.db release --id 1
python -m reservation --db inventory.db report
```

Each finite command writes one JSON object to stdout. Validation, not-found,
and conflict errors are written as JSON to stderr and use exit status 2.

## Run the HTTP server

```console
python -m reservation --db inventory.db serve --host 127.0.0.1 --port 8000
```

The server exposes `GET /health`, `GET /report`, `POST /items`,
`GET /items/<URL-encoded-sku>`, `POST /reservations`, and
`POST /reservations/<id>/release`.

## Run tests

```console
python -m unittest discover -s tests -v
```
