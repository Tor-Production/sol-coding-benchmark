# Inventory reservation service

This service uses only Python's standard library and persists inventory and
reservations in a SQLite database.

## Command line

Run commands from the repository root. The database file is created on first
use:

```console
python -m reservation --db inventory.db add --sku widget --quantity 10
python -m reservation --db inventory.db reserve --key order-123 --sku widget --quantity 2
python -m reservation --db inventory.db release --id 1
python -m reservation --db inventory.db report
```

Each finite command writes one JSON object. Validation and domain errors are
written as JSON to stderr and return exit status 2.

Start the HTTP API with:

```console
python -m reservation --db inventory.db serve --host 127.0.0.1 --port 8080
```

The server exposes `/health`, `/items`, `/reservations`, reservation release,
and `/report` as described in `TASK.md`. Stop it with Ctrl+C.

## Tests

```console
python -m unittest discover -s tests -v
```
