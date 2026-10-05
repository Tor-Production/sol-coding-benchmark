# Inventory reservation service

A persistent inventory and idempotent reservation service built with Python's
standard library and SQLite.

Run commands from the project directory:

```console
python -m reservation --db inventory.db add --sku widget --quantity 10
python -m reservation --db inventory.db reserve --key order-123 --sku widget --quantity 2
python -m reservation --db inventory.db release --id 1
python -m reservation --db inventory.db report
```

Start the HTTP server with:

```console
python -m reservation --db inventory.db serve --host 127.0.0.1 --port 8000
```

The HTTP API provides `GET /health`, item creation and lookup, reservation and
release endpoints, and `GET /report` as documented in `TASK.md`.

Run the tests with:

```console
python -m unittest discover -s tests -v
```
