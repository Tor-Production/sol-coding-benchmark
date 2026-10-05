# Inventory reservation service

Build a small service using Python's standard library and SQLite. Keep the
`reservation` package and implement store.py, http_api.py and __main__.py. No
third-party dependencies. Persistence must survive closing/reopening the store.

## Store contract

`Store(db_path)` initializes its schema. Methods and exact record shapes:

- `add_item(sku, quantity)` atomically ADDS units (also creates a new SKU),
  returning `{"sku": sku, "available": current_available}`.
- `get_item(sku)` returns that shape; missing SKU raises NotFound.
- `reserve(key, sku, quantity)` atomically checks stock and subtracts units,
  returning `{"reservation_id": <positive int>, "idempotency_key": key,
  "sku": sku, "quantity": quantity, "status": "active"}`.
- A retry with the same key and same sku/quantity returns the ORIGINAL record
  without subtracting stock again, including after release. A retry with the
  same key but different parameters raises Conflict.
- Insufficient stock raises Conflict. Missing SKU raises NotFound. Failure
  must leave both stock and reservations unchanged.
- `release(reservation_id)` restores units exactly once and returns the record
  with status `released`. Repeating release returns it without adding units.
  Missing ID raises NotFound.
- `report()` returns `{"items": [item records sorted by sku],
  "active_reservations": <count>, "reserved_units": <sum of active quantities>}`.

Export `Conflict(Exception)` and `NotFound(Exception)`. SKU and key must be
nonblank strings, stripped at boundaries. Quantities and reservation IDs must
be positive integers, excluding bool. Invalid arguments raise ValueError.
No overselling or duplicate records under concurrent calls from separate Store
instances sharing the same database. Use SQLite transactions; caller methods
must close their connections (no explicit close method required).

## HTTP contract

`create_server(db_path, host="127.0.0.1", port=0)` returns a bound standard-library
HTTPServer-compatible object. The caller starts serve_forever in a thread and
later calls shutdown/server_close. Accept JSON request objects and return JSON
with Content-Type application/json. Routes:

- GET /health -> 200 {"ok": true}
- POST /items {sku, quantity} -> 201 item record
- GET /items/<URL-encoded-sku> -> 200 item record
- POST /reservations {idempotency_key, sku, quantity} -> 201 reservation record
  (also 201 for an identical retry)
- POST /reservations/<id>/release {} -> 200 reservation record
- GET /report -> 200 report

Unknown routes and NotFound -> 404. Conflict -> 409. Malformed JSON, non-object
JSON, missing fields, invalid values -> 400. Errors are {"error": <string>}.
Bound request bodies to 64 KiB; larger Content-Length -> 413. Errors must not
kill the server. Content-Length must match response UTF-8 bytes. Concurrent
requests must obey the same stock invariants.

## CLI contract

`python -m reservation --db <path> <command>` supports:

- `add --sku <sku> --quantity <n>`
- `reserve --key <key> --sku <sku> --quantity <n>`
- `release --id <n>`
- `report`
- `serve --host 127.0.0.1 --port <n>`

Successful finite commands output one JSON record on stdout and exit 0.
Domain/validation failures output {"error": <string>} on stderr and exit 2.
serve blocks until interrupted. CLI, HTTP, and direct Store share the same DB
schema and state. Document how to run in README.md.

Run `python -m unittest discover -s tests -v`. You may add tests; preserve the
supplied tests and this contract. Finish with a brief change/test summary. No
network dependencies or subagents; local loopback HTTP testing is allowed.
