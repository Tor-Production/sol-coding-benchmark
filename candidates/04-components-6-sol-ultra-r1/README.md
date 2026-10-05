# Inventory reservation service

Requires Python 3 and uses only the standard library. The SQLite database is
created automatically at the path passed with `--db`.

```sh
python -m reservation --db stock.db add --sku widget --quantity 10
python -m reservation --db stock.db reserve --key order-1 --sku widget --quantity 2
python -m reservation --db stock.db release --id 1
python -m reservation --db stock.db report
```

Start the JSON HTTP API on a local port:

```sh
python -m reservation --db stock.db serve --host 127.0.0.1 --port 8000
```

Available routes are `GET /health`, `POST /items`, `GET /items/<sku>`,
`POST /reservations`, `POST /reservations/<id>/release`, and `GET /report`.
POST requests require a JSON object and a `Content-Length` header. The item
body contains `sku` and `quantity`; the reservation body contains
`idempotency_key`, `sku`, and `quantity`; release uses `{}`.

Run the tests with:

```sh
python -m unittest discover -s tests -v
```
