"""Command-line entry point for the reservation service."""

import argparse
import json
import sys

from .http_api import create_server
from .store import Conflict, NotFound, Store


class _JsonArgumentParser(argparse.ArgumentParser):
    def error(self, message):
        raise ValueError(message)


def _parser():
    parser = _JsonArgumentParser(prog="python -m reservation")
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

    commands.add_parser("report", help="show inventory and active reservations")

    serve = commands.add_parser("serve", help="run the HTTP service")
    serve.add_argument("--host", default="127.0.0.1")
    serve.add_argument("--port", default=0, type=int)
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
