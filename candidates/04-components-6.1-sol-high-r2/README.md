# Inventory reservation service

Python standard library only; no installation or dependencies are required.
Run commands from this directory with Python 3. SQLite creates the database
file on first use. Use the same file path for the CLI, HTTP server, and Store
to share persistent inventory and reservations.

```console
python -m reservation --db inventory.db add --sku widget --quantity 10
python -m reservation --db inventory.db reserve --key order-123 --sku widget --quantity 3
python -m reservation --db inventory.db release --id 1
python -m reservation --db inventory.db report
python -m reservation --db inventory.db serve --host 127.0.0.1 --port 8000
```

Finite commands print one JSON record and exit 0. Validation and domain errors
print a JSON error to stderr and exit 2. Stop the server with Ctrl+C.

HTTP routes return JSON:

| Method | Path | JSON request body |
| --- | --- | --- |
| GET | `/health` | None |
| POST | `/items` | `{"sku": "widget", "quantity": 10}` |
| GET | `/items/widget` | None; URL-encode the SKU in the path |
| POST | `/reservations` | `{"idempotency_key": "order-123", "sku": "widget", "quantity": 3}` |
| POST | `/reservations/1/release` | `{}` |
| GET | `/report` | None |

POST request bodies must be JSON objects and at most 64 KiB. Errors use
`{"error": "message"}` with HTTP 400 for invalid input, 404 for missing records
or routes, 409 for conflicts, and 413 for oversized bodies. Identical reservation
retries return the same reservation, including its current released status.
Releasing twice restores stock only once.

For direct use:

```python
from reservation import Store

store = Store("inventory.db")
store.add_item("widget", 10)
reservation = store.reserve("order-123", "widget", 3)
print(store.report())
store.release(reservation["reservation_id"])
```

Each operation closes its SQLite connection. Write transactions serialize
stock changes across threads and Store instances; reports read one consistent
database snapshot. SKU and key whitespace is stripped. Quantities and IDs must
be positive integers, excluding booleans.

Run all tests, including local loopback HTTP tests:

```console
python -m unittest discover -s tests -v
```
