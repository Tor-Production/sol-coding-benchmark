# Inventory reservation service

Python 3 and the standard library are the only requirements. All commands use
the same SQLite database file, so inventory and reservations persist between
invocations.

```sh
python -m reservation --db stock.db add --sku widget --quantity 10
python -m reservation --db stock.db reserve --key order-1 --sku widget --quantity 2
python -m reservation --db stock.db release --id 1
python -m reservation --db stock.db report
python -m reservation --db stock.db serve --host 127.0.0.1 --port 8000
```

The HTTP server exposes `GET /health`, `GET /items/<sku>`, `GET /report`,
`POST /items`, `POST /reservations`, and `POST /reservations/<id>/release`.
POST requests take JSON objects and return JSON responses.

Run the tests with `python -m unittest discover -s tests -v`.
