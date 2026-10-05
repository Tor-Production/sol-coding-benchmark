# Inventory reservation service

This service uses Python's standard library and a persistent SQLite database.
Run commands from the workspace directory:

```sh
python -m reservation --db stock.db add --sku widget --quantity 10
python -m reservation --db stock.db reserve --key order-1 --sku widget --quantity 2
python -m reservation --db stock.db release --id 1
python -m reservation --db stock.db report
python -m reservation --db stock.db serve --host 127.0.0.1 --port 8000
```

The server accepts JSON at `POST /items`, `POST /reservations`, and
`POST /reservations/<id>/release`. Read state with `GET /items/<sku>` and
`GET /report`; `GET /health` checks availability. For example:

```sh
curl -X POST http://127.0.0.1:8000/items -H "Content-Type: application/json" -d '{"sku":"widget","quantity":10}'
```

Run tests with `python -m unittest discover -s tests -v`.
