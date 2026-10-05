# Inventory reservation service

Python standard library only; no installation step is needed. Run commands from
this directory, choosing a SQLite file with `--db`. Each command opens and closes
its database connections; later commands and server runs reuse persisted state.

```sh
python -m reservation --db inventory.db add --sku widget --quantity 10
python -m reservation --db inventory.db reserve --key order-1 --sku widget --quantity 3
python -m reservation --db inventory.db release --id 1
python -m reservation --db inventory.db report
python -m reservation --db inventory.db serve --host 127.0.0.1 --port 8000
```

Finite commands print JSON on stdout. Validation and domain errors print JSON
on stderr and exit with status 2. Stop the server with Ctrl+C.

The server accepts JSON objects at `POST /items`, `POST /reservations`, and
`POST /reservations/<id>/release` (send `{}` for release). Use `GET /health`,
`GET /items/<URL-encoded-sku>`, and `GET /report` to read state. Request bodies
are limited to 64 KiB. Reservation keys identify retries: identical parameters
return the existing reservation, including its current status after release.

For direct access, import `Store`, `Conflict`, and `NotFound` from
`reservation.store`. Stock changes and reservation creation/release are atomic
across concurrent store instances and HTTP requests sharing the same file.

Tests: `python -m unittest discover -s tests -v`.
