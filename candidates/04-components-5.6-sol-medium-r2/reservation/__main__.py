"""Command-line entry point for the reservation service."""

import argparse
import json
import sys

from .http_api import create_server
from .store import Conflict, NotFound, Store


class _ArgumentError(Exception):
    pass


class _Parser(argparse.ArgumentParser):
    def error(self, message):
        raise _ArgumentError(message)


def _parser():
    parser = _Parser(prog="python -m reservation")
    parser.add_argument("--db", required=True)
    commands = parser.add_subparsers(dest="command", required=True)

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
    serve.add_argument("--port", default=0, type=_port)
    return parser


def _port(value):
    try:
        port = int(value)
    except ValueError:
        raise argparse.ArgumentTypeError("port must be an integer") from None
    if not 0 <= port <= 65535:
        raise argparse.ArgumentTypeError("port must be between 0 and 65535")
    return port


def _write_json(stream, value):
    stream.write(json.dumps(value, ensure_ascii=False) + "\n")


def main(argv=None):
    try:
        args = _parser().parse_args(argv)
        if args.command == "serve":
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
    except (_ArgumentError, ValueError, Conflict, NotFound) as exc:
        _write_json(sys.stderr, {"error": str(exc)})
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
