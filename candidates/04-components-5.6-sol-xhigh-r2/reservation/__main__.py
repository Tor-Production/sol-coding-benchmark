"""Command-line entry point for the inventory reservation service."""

import argparse
import json
import sys

from .http_api import create_server
from .store import Conflict, NotFound, Store


class _ArgumentError(ValueError):
    pass


class _Parser(argparse.ArgumentParser):
    def error(self, message):
        raise _ArgumentError(message)


def _build_parser():
    parser = _Parser(prog="python -m reservation")
    parser.add_argument("--db", required=True, help="path to the SQLite database")
    commands = parser.add_subparsers(dest="command", required=True)

    add = commands.add_parser("add", help="add units to an SKU")
    add.add_argument("--sku", required=True)
    add.add_argument("--quantity", required=True, type=int)

    reserve = commands.add_parser("reserve", help="reserve units")
    reserve.add_argument("--key", required=True)
    reserve.add_argument("--sku", required=True)
    reserve.add_argument("--quantity", required=True, type=int)

    release = commands.add_parser("release", help="release a reservation")
    release.add_argument("--id", required=True, type=int, dest="reservation_id")

    commands.add_parser("report", help="show inventory and reservation totals")

    serve = commands.add_parser("serve", help="serve the HTTP API")
    serve.add_argument("--host", default="127.0.0.1")
    serve.add_argument("--port", default=0, type=int)
    return parser


def _print_json(value, stream):
    print(json.dumps(value, ensure_ascii=False, separators=(",", ":")), file=stream)


def main(argv=None):
    try:
        args = _build_parser().parse_args(argv)

        if args.command == "serve":
            if args.port < 0 or args.port > 65535:
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
            result = store.release(args.reservation_id)
        else:
            result = store.report()

        _print_json(result, sys.stdout)
        return 0
    except (_ArgumentError, Conflict, NotFound, ValueError) as error:
        _print_json({"error": str(error)}, sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
