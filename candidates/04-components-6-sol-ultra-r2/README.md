# Inventory reservation service

Python standard library and SQLite only. All commands and HTTP requests using
the same database path share inventory and reservations. The database is created
on first use and persists across processes.

## Command line

```text
python -m reservation --db stock.db add --sku widget --quantity 10
python -m reservation --db stock.db reserve --key order-1 --sku widget --quantity 2
python -m reservation --db stock.db release --id 1
python -m reservation --db stock.db report
python -m reservation --db stock.db serve --host 127.0.0.1 --port 8000
```

Finite commands print one JSON record. Validation and domain errors print one
JSON error to stderr and exit with status 2. Stop the HTTP server with Ctrl+C.

## HTTP

The server accepts JSON objects for POST requests and returns JSON responses:

| Method | Path | Request body |
| --- | --- | --- |
| GET | `/health` | |
| POST | `/items` | `{"sku":"widget","quantity":10}` |
| GET | `/items/widget` | |
| POST | `/reservations` | `{"idempotency_key":"order-1","sku":"widget","quantity":2}` |
| POST | `/reservations/1/release` | `{}` |
| GET | `/report` | |

URL-encode SKUs in GET paths. POST bodies are limited to 64 KiB. A reservation
key can be retried with the same SKU and quantity without changing stock.

Run tests with `python -m unittest discover -s tests -v`.
