# Inventory reservation service

Uses Python's standard library and SQLite; no installation or dependencies are
required. Run commands from this directory with Python 3. The database is created
automatically and persists between processes.

```console
python -m reservation --db inventory.db add --sku widget --quantity 10
python -m reservation --db inventory.db reserve --key order-123 --sku widget --quantity 3
python -m reservation --db inventory.db release --id 1
python -m reservation --db inventory.db report
python -m reservation --db inventory.db serve --host 127.0.0.1 --port 8000
```

Finite commands print one JSON record to stdout. Validation and domain errors
print a JSON error to stderr and exit with status 2. Stop the server with Ctrl+C.

The HTTP service accepts JSON objects and returns JSON:

| Method | Route | Request object | Success |
| --- | --- | --- | --- |
| GET | `/health` | | 200 |
| POST | `/items` | `{"sku":"widget","quantity":10}` | 201 |
| GET | `/items/<URL-encoded-sku>` | | 200 |
| POST | `/reservations` | `{"idempotency_key":"order-123","sku":"widget","quantity":3}` | 201 |
| POST | `/reservations/1/release` | `{}` | 200 |
| GET | `/report` | | 200 |

For example, read `http://127.0.0.1:8000/report` in a browser. HTTP errors contain
`{"error":"..."}` and use status 400 for invalid input, 404 for missing records
or routes, 409 for conflicts, and 413 for bodies over 64 KiB.

SKU and idempotency key strings are trimmed. Retrying a reservation key with the
same parameters returns the existing reservation, including its current status
after release. Different parameters conflict. Releases restore stock once.
SQLite transactions serialize stock changes across threads and processes.

The same database can also be used directly:

```python
from reservation import Store, Conflict, NotFound

store = Store("inventory.db")
print(store.report())
```

Tests: `python -m unittest discover -s tests -v`.
