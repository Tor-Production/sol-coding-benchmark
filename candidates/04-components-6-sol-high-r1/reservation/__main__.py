"""Command-line entry point for the reservation service."""

import argparse
import json
import sys

from .http_api import create_server
from .store import Conflict, NotFound, Store


class _Parser(argparse.ArgumentParser):
    def error(self, message):
        raise ValueError(message)


def _parser():
    parser = _Parser(prog="python -m reservation")
    parser.add_argument("--db", required=True, help="SQLite database path")
    commands = parser.add_subparsers(dest="command", required=True, parser_class=_Parser)

    add = commands.add_parser("add")
    add.add_argument("--sku", required=True)
    add.add_argument("--quantity", required=True, type=int)

    reserve = commands.add_parser("reserve")
    reserve.add_argument("--key", required=True)
    reserve.add_argument("--sku", required=True)
    reserve.add_argument("--quantity", required=True, type=int)

    release = commands.add_parser("release")
    release.add_argument("--id", required=True, type=int)

    commands.add_parser("report")

    serve = commands.add_parser("serve")
    serve.add_argument("--host", default="127.0.0.1")
    serve.add_argument("--port", default=8000, type=int)
    return parser


def main(argv=None):
    try:
        args = _parser().parse_args(argv)
        if args.command == "serve":
            if not 0 <= args.port <= 65535:
                raise ValueError("port must be between 0 and 65535")
            if not args.host.strip():
                raise ValueError("host must be a nonblank string")
            with create_server(args.db, args.host, args.port) as server:
                try:
                    server.serve_forever()
                except KeyboardInterrupt:
                    pass
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
        print(json.dumps(result, ensure_ascii=False))
        return 0
    except (ValueError, Conflict, NotFound) as exc:
        print(json.dumps({"error": str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
