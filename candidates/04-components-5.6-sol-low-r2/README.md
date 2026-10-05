# Inventory reservation service

The service uses only Python's standard library and persists state in SQLite.

Run tests with `python -m unittest discover -s tests -v`.

Example CLI usage:

```console
python -m reservation --db inventory.db add --sku widget --quantity 10
python -m reservation --db inventory.db reserve --key order-1 --sku widget --quantity 2
python -m reservation --db inventory.db release --id 1
python -m reservation --db inventory.db report
python -m reservation --db inventory.db serve --host 127.0.0.1 --port 8000
```

The HTTP API provides `GET /health`, item creation and lookup under `/items`,
reservation creation and release under `/reservations`, and `GET /report`.
