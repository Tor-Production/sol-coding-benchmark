# Inventory reservation service

Python standard library only; no installation or dependencies required.

Run commands from this directory with a shared SQLite database path:

```sh
python -m reservation --db stock.db add --sku widget --quantity 10
python -m reservation --db stock.db reserve --key order-1 --sku widget --quantity 2
python -m reservation --db stock.db release --id 1
python -m reservation --db stock.db report
python -m reservation --db stock.db serve --host 127.0.0.1 --port 8000
```

Finite commands print JSON on stdout. Validation and domain failures print a
JSON error on stderr and exit with code 2. Stop the HTTP server with Ctrl+C.

The HTTP API accepts JSON objects at `POST /items`, `POST /reservations`, and
`POST /reservations/<id>/release`. Read with `GET /health`, `GET /items/<sku>`
(URL-encode the SKU), and `GET /report`. Request bodies are limited to 64 KiB.

Direct Python access uses `from reservation.store import Store, Conflict,
NotFound` and `Store("stock.db")`. The CLI, HTTP API, and direct calls share
persistent state. Each store operation closes its SQLite connection; write
transactions serialize stock changes across instances. Reservation keys are
unique: identical retries return the existing record (including its released
status), while changing the SKU or quantity conflicts. Release restores units
only once.

Tests: `python -m unittest discover -s tests -v`.

If a restricted Windows sandbox blocks Python 3.14 temporary-directory ACLs,
`python run_tests_local.py` runs the same unchanged suite using workspace-local
temporary directories with inherited permissions. This workaround is confined
to the test runner process.
