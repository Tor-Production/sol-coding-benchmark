# Inventory reservation service

Uses Python's standard library and SQLite; no installation is needed.

```sh
python -m reservation --db stock.db add --sku widget --quantity 10
python -m reservation --db stock.db reserve --key order-1 --sku widget --quantity 2
python -m reservation --db stock.db release --id 1
python -m reservation --db stock.db report
python -m reservation --db stock.db serve --host 127.0.0.1 --port 8000
```

Finite commands print JSON. Validation and domain errors print JSON to stderr
and exit with status 2. Stop the server with Ctrl+C. Reuse the same database path
to preserve state across CLI commands, HTTP requests, and `Store` instances.

HTTP routes are `GET /health`, `POST /items`, `GET /items/<encoded-sku>`,
`POST /reservations`, `POST /reservations/<id>/release`, and `GET /report`.
POST bodies are JSON objects; item creation needs `sku` and `quantity`, and
reservation creation also needs `idempotency_key`. Release accepts `{}`.
Bodies are limited to 64 KiB. Reservation keys are retained after release;
identical retries return the existing record and different parameters conflict.

Run tests with `python -m unittest discover -s tests -v`.
