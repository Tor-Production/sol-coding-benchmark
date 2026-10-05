# Inventory reservation service

This service uses Python's standard library and a SQLite database file. Use the
same `--db` path for each command or server process to share inventory state.

```sh
python -m reservation --db stock.db add --sku widget --quantity 10
python -m reservation --db stock.db reserve --key order-123 --sku widget --quantity 2
python -m reservation --db stock.db release --id 1
python -m reservation --db stock.db report
python -m reservation --db stock.db serve --host 127.0.0.1 --port 8000
```

The HTTP server accepts JSON objects. Its routes are `GET /health`,
`POST /items`, `GET /items/<URL-encoded-sku>`, `POST /reservations`,
`POST /reservations/<id>/release`, and `GET /report`. For example:

```sh
curl -X POST http://127.0.0.1:8000/items \
  -H 'Content-Type: application/json' \
  -d '{"sku":"widget","quantity":10}'
```

Run tests with `python -m unittest discover -s tests -v`.
