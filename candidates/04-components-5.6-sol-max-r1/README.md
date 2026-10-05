# Inventory reservation service

This package provides a persistent SQLite inventory store, a JSON HTTP API,
and a command-line interface. It uses only the Python standard library.

## Command line

Place the database option before the command:

```console
python -m reservation --db inventory.db add --sku widget --quantity 10
python -m reservation --db inventory.db reserve --key order-123 --sku widget --quantity 2
python -m reservation --db inventory.db release --id 1
python -m reservation --db inventory.db report
```

Each finite command writes one JSON object to standard output. Validation,
not-found, and conflict errors are JSON objects on standard error and use exit
status 2.

Start the HTTP service with:

```console
python -m reservation --db inventory.db serve --host 127.0.0.1 --port 8000
```

The service exposes `GET /health`, `GET /report`, `POST /items`,
`GET /items/<URL-encoded-sku>`, `POST /reservations`, and
`POST /reservations/<id>/release`. POST bodies are JSON objects and are limited
to 64 KiB.

## Tests

Run the test suite from this directory:

```console
python -m unittest discover -s tests -v
```
