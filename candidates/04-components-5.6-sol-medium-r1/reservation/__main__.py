"""Command-line entry point for ``python -m reservation``."""

import argparse
import json
import sys

from .http_api import create_server
from .store import Conflict, NotFound, Store


class _Parser(argparse.ArgumentParser):
    def error(self, message):
        raise ValueError(message)


def _integer(value):
    try:
        return int(value)
    except (TypeError, ValueError):
        raise argparse.ArgumentTypeError("must be an integer") from None


def _parser():
    parser = _Parser(prog="python -m reservation")
    parser.add_argument("--db", required=True)
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


def _print_json(stream, value):
    print(json.dumps(value, separators=(",", ":")), file=stream)


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
        _print_json(sys.stdout, result)
        return 0
    except (ValueError, Conflict, NotFound, OSError) as error:
        _print_json(sys.stderr, {"error": str(error)})
        return 2


if __name__ == "__main__":
    sys.exit(main())
