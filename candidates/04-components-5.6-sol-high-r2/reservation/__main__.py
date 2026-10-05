"""Command line interface for ``python -m reservation``."""

import argparse
import json
import sqlite3
import sys

from .http_api import create_server
from .store import Conflict, NotFound, Store


class _ArgumentParser(argparse.ArgumentParser):
    def error(self, message):
        raise ValueError(message)


def _integer(value):
    try:
        return int(value, 10)
    except ValueError:
        raise argparse.ArgumentTypeError("must be an integer") from None


def _parser():
    parser = _ArgumentParser(prog="python -m reservation")
    parser.add_argument("--db", required=True, help="path to the SQLite database")
    commands = parser.add_subparsers(dest="command", required=True)

    add = commands.add_parser("add")
    add.add_argument("--sku", required=True)
    add.add_argument("--quantity", required=True, type=_integer)

    reserve = commands.add_parser("reserve")
    reserve.add_argument("--key", required=True)
    reserve.add_argument("--sku", required=True)
    reserve.add_argument("--quantity", required=True, type=_integer)

    release = commands.add_parser("release")
    release.add_argument("--id", required=True, type=_integer)

    commands.add_parser("report")

    serve = commands.add_parser("serve")
    serve.add_argument("--host", default="127.0.0.1")
    serve.add_argument("--port", default=0, type=_integer)
    return parser


def _write_json(stream, value):
    json.dump(value, stream, ensure_ascii=False, separators=(",", ":"))
    stream.write("\n")


def main(argv=None):
    try:
        args = _parser().parse_args(argv)

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
            record = store.add_item(args.sku, args.quantity)
        elif args.command == "reserve":
            record = store.reserve(args.key, args.sku, args.quantity)
        elif args.command == "release":
            record = store.release(args.id)
        else:
            record = store.report()
        _write_json(sys.stdout, record)
        return 0
    except (Conflict, NotFound, ValueError, sqlite3.Error, OSError) as error:
        _write_json(sys.stderr, {"error": str(error) or type(error).__name__})
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
