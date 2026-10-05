# Inventory reservation service

This package provides a persistent SQLite inventory store, JSON HTTP API, and
command-line interface using only the Python standard library.

## Command line

Run commands from the project directory. Every command uses the database given
by `--db`; it is created automatically if it does not exist.

```console
python -m reservation --db inventory.db add --sku widget --quantity 10
python -m reservation --db inventory.db reserve --key order-123 --sku widget --quantity 2
python -m reservation --db inventory.db release --id 1
python -m reservation --db inventory.db report
```

Finite commands print one JSON object. Validation, missing-record, and conflict
errors print a JSON error object to stderr and exit with status 2.

Start the HTTP service with:

```console
python -m reservation --db inventory.db serve --host 127.0.0.1 --port 8000
```

The API includes `GET /health`, `POST /items`, `GET /items/<sku>`,
`POST /reservations`, `POST /reservations/<id>/release`, and `GET /report`.
POST request bodies are JSON objects. Stop the server with Ctrl+C.

## Tests

```console
python -m unittest discover -s tests -v
```
