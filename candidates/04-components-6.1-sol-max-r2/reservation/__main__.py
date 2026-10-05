"""Command-line inventory operations and HTTP server entry point."""

import argparse
import json
import sqlite3
import sys

from .http_api import create_server
from .store import Conflict, NotFound, Store


def _print_error(message):
    print(json.dumps({"error": str(message)}), file=sys.stderr)


class _Parser(argparse.ArgumentParser):
    def error(self, message):
        _print_error(message)
        self.exit(2)


def _parser():
    parser = _Parser(description="Persistent inventory reservations")
    parser.add_argument("--db", required=True, help="Path to the SQLite database")
    commands = parser.add_subparsers(dest="command", required=True)

    add = commands.add_parser("add", help="Add units to an item")
    add.add_argument("--sku", required=True)
    add.add_argument("--quantity", required=True, type=int)

    reserve = commands.add_parser("reserve", help="Reserve available units")
    reserve.add_argument("--key", required=True)
    reserve.add_argument("--sku", required=True)
    reserve.add_argument("--quantity", required=True, type=int)

    release = commands.add_parser("release", help="Release a reservation once")
    release.add_argument("--id", required=True, type=int)

    commands.add_parser("report", help="Show inventory and active reservation totals")

    serve = commands.add_parser("serve", help="Run the JSON HTTP server")
    serve.add_argument("--host", default="127.0.0.1")
    serve.add_argument("--port", type=int, default=8000)
    return parser


def main(argv=None):
    args = _parser().parse_args(argv)
    try:
        if args.command == "serve":
            if not 0 <= args.port <= 65535:
                raise ValueError("port must be between 0 and 65535")
            with create_server(args.db, host=args.host, port=args.port) as server:
                try:
                    server.serve_forever()
                except KeyboardInterrupt:
                    pass
            return 0

        store = Store(args.db)
        if args.command == "add":
            record = store.add_item(args.sku, args.quantity)
        elif args.command == "reserve":
            record = store.reserve(args.key, args.sku, args.quantity)
        elif args.command == "release":
            record = store.release(args.id)
        else:
            record = store.report()
        print(json.dumps(record))
        return 0
    except (Conflict, NotFound, ValueError, sqlite3.Error, OSError) as error:
        _print_error(error)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
