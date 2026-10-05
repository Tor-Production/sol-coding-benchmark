# Inventory reservation service

Requires Python 3 with SQLite support. No packages need to be installed.

```sh
python -m reservation --db stock.db add --sku widget --quantity 10
python -m reservation --db stock.db reserve --key order-1 --sku widget --quantity 2
python -m reservation --db stock.db release --id 1
python -m reservation --db stock.db report
python -m reservation --db stock.db serve --host 127.0.0.1 --port 8000
```

The HTTP server exposes `/health`, `/items`, `/reservations`, and `/report`. Stop `serve` with Ctrl+C.

Run tests with `python -m unittest discover -s tests -v`.
