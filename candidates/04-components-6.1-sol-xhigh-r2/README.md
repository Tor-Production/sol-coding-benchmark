# Inventory reservation service

An inventory reservation service using Python's standard library and SQLite.
No dependency installation is required. Run commands from this directory with
Python 3.9 or newer, using the same database file across all interfaces.

```sh
python -m reservation --db inventory.db add --sku widget --quantity 10
python -m reservation --db inventory.db reserve --key order-123 --sku widget --quantity 3
python -m reservation --db inventory.db report
python -m reservation --db inventory.db release --id 1
python -m reservation --db inventory.db serve --host 127.0.0.1 --port 8000
```

Finite commands print one JSON result to stdout. Validation or domain errors
print a JSON error to stderr and exit with status 2. Stop the server with Ctrl+C.
The server defaults to `127.0.0.1:8000` when host and port are omitted.

The HTTP API accepts JSON objects and returns JSON:

| Method | Path | Request body | Success status |
| --- | --- | --- | --- |
| GET | `/health` | | 200 |
| POST | `/items` | `{"sku":"widget","quantity":10}` | 201 |
| GET | `/items/widget` | | 200 |
| POST | `/reservations` | `{"idempotency_key":"order-123","sku":"widget","quantity":3}` | 201 |
| POST | `/reservations/1/release` | `{}` | 200 |
| GET | `/report` | | 200 |

URL-encode the entire SKU when placing it in a path. Request bodies are limited
to 64 KiB. Errors have the shape `{"error":"message"}`: invalid requests return
400, missing records or routes 404, conflicts 409, and oversized bodies 413.

Direct Python access uses the same state:

```python
from reservation.store import Store, Conflict, NotFound
from reservation.http_api import create_server

store = Store("inventory.db")
store.add_item("widget", 10)
reservation = store.reserve("order-456", "widget", 3)
store.release(reservation["reservation_id"])
print(store.report())

# For an embedded server, create_server binds immediately; port=0 selects a
# free port. Run serve_forever in a thread, then shutdown and server_close.
```

SKUs and idempotency keys are stripped of surrounding whitespace. Quantities
and reservation IDs must be positive integers, excluding booleans. Adding an
item adds units to its current stock. Retrying a key with identical parameters
returns the existing reservation, including its released status after release;
different parameters conflict. Releasing a reservation restores stock once.
Reports sort items by SKU and count only active reservations.

Each store operation closes its SQLite connection. Transactions serialize
stock changes across threads, processes, and separate Store instances, while
reports use a consistent snapshot. Inventory and reservations persist in the
database file after a store or server is recreated.

Run the tests, including local loopback HTTP and CLI checks:

```sh
python -m unittest discover -s tests -v
```
