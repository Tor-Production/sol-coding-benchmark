# Inventory reservation service

A persistent inventory and idempotent reservation service built with Python's
standard library and SQLite.

Run commands from this directory:

```console
python -m reservation --db inventory.db add --sku widget --quantity 10
python -m reservation --db inventory.db reserve --key order-123 --sku widget --quantity 2
python -m reservation --db inventory.db release --id 1
python -m reservation --db inventory.db report
```

Start the HTTP API (port `0` asks the OS to select an available port):

```console
python -m reservation --db inventory.db serve --host 127.0.0.1 --port 8080
```

Run the test suite:

```console
python -m unittest discover -s tests -v
```
