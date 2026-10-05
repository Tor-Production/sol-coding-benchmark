"""Command-line access to the shared SQLite store and HTTP service."""

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
    parser = _Parser(description="Inventory reservation service")
    parser.add_argument("--db", required=True, help="SQLite database path")
    commands = parser.add_subparsers(dest="command", required=True)

    add = commands.add_parser("add", help="Add units to an item")
    add.add_argument("--sku", required=True)
    add.add_argument("--quantity", type=int, required=True)

    reserve = commands.add_parser("reserve", help="Reserve units using an idempotency key")
    reserve.add_argument("--key", required=True)
    reserve.add_argument("--sku", required=True)
    reserve.add_argument("--quantity", type=int, required=True)

    release = commands.add_parser("release", help="Release a reservation")
    release.add_argument("--id", type=int, required=True)

    commands.add_parser("report", help="Report available and reserved stock")

    serve = commands.add_parser("serve", help="Run the HTTP service until interrupted")
    serve.add_argument("--host", default="127.0.0.1")
    serve.add_argument("--port", type=int, default=8000)
    return parser


def main(argv=None):
    try:
        args = _parser().parse_args(argv)
        if args.command == "serve":
            if not 0 <= args.port <= 65535:
                raise ValueError("port must be between 0 and 65535")
            server = create_server(args.db, args.host, args.port)
            try:
                server.serve_forever()
            except KeyboardInterrupt:
                pass
            finally:
                server.server_close()
            return 0

        store = Store(args.db)
        if args.command == "add":
            result = store.add_item(args.sku, args.quantity)
        elif args.command == "reserve":
            result = store.reserve(args.key, args.sku, args.quantity)
        elif args.command == "release":
            result = store.release(args.id)
        else:
            result = store.report()
        print(json.dumps(result))
        return 0
    except (Conflict, NotFound, ValueError, sqlite3.Error, OSError, OverflowError) as error:
        print(json.dumps({"error": str(error)}), file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
