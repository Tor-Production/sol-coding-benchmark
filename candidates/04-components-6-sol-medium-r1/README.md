# Inventory reservation service

Python standard library and SQLite only. The database is created automatically at
the path passed with `--db`. Commands, HTTP requests, and direct `Store` calls
can share that path.

```sh
python -m reservation --db stock.db add --sku widget --quantity 10
python -m reservation --db stock.db reserve --key order-1 --sku widget --quantity 2
python -m reservation --db stock.db release --id 1
python -m reservation --db stock.db report
python -m reservation --db stock.db serve --host 127.0.0.1 --port 8000
```

The server accepts JSON requests at `/items`, `/reservations`, and
`/reservations/<id>/release`; it also serves GET `/health`, `/items/<sku>`,
and `/report`. URL-encode SKU path segments. Successful finite CLI commands
print one JSON record; errors print JSON to stderr.

Run tests with `python -m unittest discover -s tests -v`.
