# Inventory reservation service

Python standard library and SQLite only. The database is created on first use.

```sh
python -m reservation --db inventory.db add --sku widget --quantity 10
python -m reservation --db inventory.db reserve --key order-1 --sku widget --quantity 2
python -m reservation --db inventory.db release --id 1
python -m reservation --db inventory.db report
python -m reservation --db inventory.db serve --host 127.0.0.1 --port 8000
```

The HTTP API has `GET /health`, `GET /items/<sku>`, `GET /report`, `POST /items`, `POST /reservations`, and `POST /reservations/<id>/release`. POST bodies are JSON objects.

Run tests with `python -m unittest discover -s tests -v`.
