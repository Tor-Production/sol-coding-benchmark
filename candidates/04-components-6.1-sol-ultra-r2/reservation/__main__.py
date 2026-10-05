"""Command-line entry point for the inventory reservation service."""

import argparse
import json
import sqlite3
import sys

from .http_api import create_server
from .store import Conflict, NotFound, Store


class _Parser(argparse.ArgumentParser):
    def error(self, message):
        raise ValueError(message)


def _parser():
    parser = _Parser(description="SQLite inventory reservation service")
    parser.add_argument("--db", required=True, help="SQLite database file")
    commands = parser.add_subparsers(dest="command", required=True)

    add = commands.add_parser("add", help="Add stock to an item")
    add.add_argument("--sku", required=True)
    add.add_argument("--quantity", type=int, required=True)

    reserve = commands.add_parser("reserve", help="Reserve stock using an idempotency key")
    reserve.add_argument("--key", required=True)
    reserve.add_argument("--sku", required=True)
    reserve.add_argument("--quantity", type=int, required=True)

    release = commands.add_parser("release", help="Release a reservation once")
    release.add_argument("--id", type=int, required=True)

    commands.add_parser("report", help="Report stock and active reservations")

    serve = commands.add_parser("serve", help="Run the HTTP API until interrupted")
    serve.add_argument("--host", default="127.0.0.1")
    serve.add_argument("--port", type=int, default=8000)
    return parser


def main(argv=None):
    try:
        arguments = _parser().parse_args(argv)
        if arguments.command == "serve":
            if not 0 <= arguments.port <= 65535:
                raise ValueError("port must be between 0 and 65535")
            with create_server(arguments.db, arguments.host, arguments.port) as server:
                try:
                    server.serve_forever()
                except KeyboardInterrupt:
                    pass
            return 0

        store = Store(arguments.db)
        if arguments.command == "add":
            record = store.add_item(arguments.sku, arguments.quantity)
        elif arguments.command == "reserve":
            record = store.reserve(arguments.key, arguments.sku, arguments.quantity)
        elif arguments.command == "release":
            record = store.release(arguments.id)
        else:
            record = store.report()
        print(json.dumps(record))
        return 0
    except (Conflict, NotFound, ValueError, sqlite3.Error, OSError) as error:
        print(json.dumps({"error": str(error)}), file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
