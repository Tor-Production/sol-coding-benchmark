# Inventory reservation service

This service stores stock and reservations in SQLite. It needs Python's standard
library only. All interfaces use the same database file, so data persists across
commands and server restarts.

Run commands from the project directory:

```sh
python -m reservation --db stock.db add --sku widget --quantity 10
python -m reservation --db stock.db reserve --key order-1 --sku widget --quantity 2
python -m reservation --db stock.db release --id 1
python -m reservation --db stock.db report
python -m reservation --db stock.db serve --host 127.0.0.1 --port 8000
```

The server exposes `GET /health`, `GET /report`, `GET /items/<sku>`,
`POST /items`, `POST /reservations`, and
`POST /reservations/<id>/release`. POST requests take JSON objects. For example:

```sh
curl -X POST http://127.0.0.1:8000/items -H "Content-Type: application/json" -d '{"sku":"widget","quantity":10}'
curl -X POST http://127.0.0.1:8000/reservations -H "Content-Type: application/json" -d '{"idempotency_key":"order-1","sku":"widget","quantity":2}'
```

Run tests with `python -m unittest discover -s tests -v`.
