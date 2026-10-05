# Inventory reservation service

Python standard library and SQLite only. The database file is created on first use
and can be reopened by the CLI, HTTP server, or `reservation.store.Store`.

```sh
python -m reservation --db inventory.db add --sku widget --quantity 10
python -m reservation --db inventory.db reserve --key order-1 --sku widget --quantity 2
python -m reservation --db inventory.db release --id 1
python -m reservation --db inventory.db report
python -m reservation --db inventory.db serve --host 127.0.0.1 --port 8000
```

The server accepts JSON on `POST /items`, `POST /reservations`, and
`POST /reservations/<id>/release`. It also serves `GET /health`,
`GET /items/<URL-encoded-sku>`, and `GET /report`.

Run tests with `python -m unittest discover -s tests -v`.
