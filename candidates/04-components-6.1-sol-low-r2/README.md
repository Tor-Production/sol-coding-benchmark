# Inventory reservation service

Python standard library only; no installation or dependencies are needed.
Run commands from this directory, using the same database path to share state:

```sh
python -m reservation --db stock.db add --sku widget --quantity 10
python -m reservation --db stock.db reserve --key order-1 --sku widget --quantity 2
python -m reservation --db stock.db release --id 1
python -m reservation --db stock.db report
python -m reservation --db stock.db serve --host 127.0.0.1 --port 8000
```

Finite commands print JSON; validation and domain errors print JSON to stderr
and exit with code 2. Stop the HTTP server with Ctrl+C.

The API accepts JSON objects at `POST /items`, `POST /reservations`, and
`POST /reservations/<id>/release` (send `{}`). Read stock using
`GET /items/<URL-encoded-sku>`, totals using `GET /report`, and health using
`GET /health`. Request bodies are limited to 64 KiB.

Direct Python callers can use `from reservation.store import Store, Conflict,
NotFound`. Each operation opens and closes its SQLite connection. Transactions
serialize writes across instances, and retries keep their original reservation
ID and current status even after release. Use a file database for persistence.

Run tests: `python -m unittest discover -s tests -v`.
On a restricted Windows sandbox where Python 3.14 temporary-directory ACLs
deny access, `python run_workspace_tests.py` runs the same discovery with
workspace-local temporary directories that inherit workspace permissions.
