# Inventory reservation service

Persistent inventory and idempotent reservations using Python's standard library
and SQLite. No dependencies need to be installed. Run commands from this directory.

```sh
python -m reservation --db stock.db add --sku widget --quantity 10
python -m reservation --db stock.db reserve --key order-123 --sku widget --quantity 3
python -m reservation --db stock.db release --id 1
python -m reservation --db stock.db report
```

Finite commands print one JSON record to stdout. Validation and domain errors
print a JSON error to stderr and exit with code 2. Use the same database path
across commands to retain inventory and reservation history.

Start the HTTP server and stop it with Ctrl+C:

```sh
python -m reservation --db stock.db serve --host 127.0.0.1 --port 8000
```

The API accepts JSON objects and returns JSON, including errors:

| Method | Route | Request body |
| --- | --- | --- |
| GET | `/health` | — |
| POST | `/items` | `{"sku": "widget", "quantity": 10}` |
| GET | `/items/<URL-encoded-sku>` | — |
| POST | `/reservations` | `{"idempotency_key": "order-123", "sku": "widget", "quantity": 3}` |
| POST | `/reservations/<id>/release` | `{}` |
| GET | `/report` | — |

Request bodies are limited to 64 KiB. Item and reservation creation return 201;
reads and releases return 200. Invalid input returns 400, missing records or routes
return 404, stock or key conflicts return 409, and oversized bodies return 413.

Direct Python use shares the same state:

```python
from reservation import Store

store = Store("stock.db")
print(store.get_item("widget"))
print(store.report())
```

SKUs and keys are stripped of surrounding whitespace. Quantities and reservation
IDs must be positive integers; booleans are rejected. Adding units is cumulative.
Repeating a reservation key with identical parameters returns that reservation
with its current status, including after release. Different parameters conflict.
Releasing a reservation restores units once. Reports sort items by SKU and count
only active reservations. Transactions serialize stock changes across separate
store instances and HTTP requests; each operation closes its SQLite connection.

Run the tests:

```sh
python -m unittest discover -s tests -v
```
