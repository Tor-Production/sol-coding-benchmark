# Inventory reservation service

Requires Python 3.9 or newer and uses only the standard library. All interfaces
share one SQLite database file; data persists between commands and restarts.

```sh
python -m reservation --db stock.db add --sku widget --quantity 10
python -m reservation --db stock.db reserve --key order-1 --sku widget --quantity 2
python -m reservation --db stock.db release --id 1
python -m reservation --db stock.db report
python -m reservation --db stock.db serve --host 127.0.0.1 --port 8000
```

The HTTP server accepts JSON objects at `POST /items`, `POST /reservations`,
and `POST /reservations/<id>/release`. Read state with `GET /items/<sku>` and
`GET /report`; `GET /health` checks availability. An identical reservation
request replays its original response, including after release.

Run the tests with `python -m unittest discover -s tests -v`.
