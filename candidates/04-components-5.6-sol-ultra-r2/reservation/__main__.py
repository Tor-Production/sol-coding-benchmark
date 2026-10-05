"""Command-line interface for the inventory reservation service."""

import argparse
import json
import sys

from .http_api import create_server
from .store import Conflict, NotFound, Store


class _ArgumentError(ValueError):
    pass


class _JSONArgumentParser(argparse.ArgumentParser):
    def error(self, message):
        raise _ArgumentError(message)


def _port(value):
    try:
        number = int(value, 10)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("must be an integer") from exc
    if not 0 <= number <= 65535:
        raise argparse.ArgumentTypeError("must be between 0 and 65535")
    return number


def _build_parser():
    parser = _JSONArgumentParser(prog="python -m reservation")
    parser.add_argument("--db", required=True, help="path to the SQLite database")
    commands = parser.add_subparsers(dest="command", required=True)

    add = commands.add_parser("add", help="add inventory")
    add.add_argument("--sku", required=True)
    add.add_argument("--quantity", required=True, type=int)

    reserve = commands.add_parser("reserve", help="reserve inventory")
    reserve.add_argument("--key", required=True)
    reserve.add_argument("--sku", required=True)
    reserve.add_argument("--quantity", required=True, type=int)

    release = commands.add_parser("release", help="release a reservation")
    release.add_argument("--id", required=True, type=int)

    commands.add_parser("report", help="show inventory and reservation totals")

    serve = commands.add_parser("serve", help="run the HTTP service")
    serve.add_argument("--host", default="127.0.0.1")
    serve.add_argument("--port", default=0, type=_port)
    return parser


def _write_json(stream, value):
    stream.write(json.dumps(value, ensure_ascii=False, separators=(",", ":")))
    stream.write("\n")


def main(argv=None):
    try:
        args = _build_parser().parse_args(argv)

        if args.command == "serve":
            server = create_server(args.db, host=args.host, port=args.port)
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
    except (Conflict, NotFound, ValueError) as exc:
        _write_json(sys.stderr, {"error": str(exc)})
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
