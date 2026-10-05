"""Command-line interface for the inventory reservation service."""

import argparse
import json
import sqlite3
import sys

from .http_api import create_server
from .store import Conflict, NotFound, Store


def _write_json(stream, value):
    stream.write(json.dumps(value, ensure_ascii=False, separators=(",", ":")))
    stream.write("\n")


class _JSONArgumentParser(argparse.ArgumentParser):
    def error(self, message):
        _write_json(sys.stderr, {"error": message})
        raise SystemExit(2)


def _parser():
    parser = _JSONArgumentParser(
        prog="python -m reservation",
        description="Manage inventory reservations.",
        allow_abbrev=False,
    )
    parser.add_argument("--db", required=True, help="path to the SQLite database")
    commands = parser.add_subparsers(dest="command", required=True)

    add = commands.add_parser("add", help="add stock", allow_abbrev=False)
    add.add_argument("--sku", required=True)
    add.add_argument("--quantity", required=True, type=int)

    reserve = commands.add_parser(
        "reserve", help="reserve stock", allow_abbrev=False
    )
    reserve.add_argument("--key", required=True)
    reserve.add_argument("--sku", required=True)
    reserve.add_argument("--quantity", required=True, type=int)

    release = commands.add_parser(
        "release", help="release a reservation", allow_abbrev=False
    )
    release.add_argument("--id", required=True, type=int)

    commands.add_parser("report", help="show inventory report", allow_abbrev=False)

    serve = commands.add_parser("serve", help="serve the HTTP API", allow_abbrev=False)
    serve.add_argument("--host", default="127.0.0.1")
    serve.add_argument("--port", default=8000, type=int)
    return parser


def main(argv=None):
    args = _parser().parse_args(argv)
    try:
        if args.command == "serve":
            if not args.host.strip():
                raise ValueError("host must be a nonblank string")
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
        _write_json(sys.stdout, result)
        return 0
    except (Conflict, NotFound, ValueError, OverflowError, sqlite3.Error, OSError) as exc:
        _write_json(sys.stderr, {"error": str(exc) or exc.__class__.__name__})
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
