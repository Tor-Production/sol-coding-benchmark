"""Command line interface for the inventory reservation service."""

import argparse
import json
import sys

from .http_api import create_server
from .store import Conflict, NotFound, Store


class JSONArgumentParser(argparse.ArgumentParser):
    def error(self, message):
        print(json.dumps({"error": message}), file=sys.stderr)
        raise SystemExit(2)


def main(argv=None):
    parser = JSONArgumentParser(prog="python -m reservation")
    parser.add_argument("--db", required=True, help="SQLite database path")
    commands = parser.add_subparsers(
        dest="command", required=True, parser_class=JSONArgumentParser
    )

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
    serve.add_argument("--port", type=int, default=8000)

    args = parser.parse_args(argv)
    try:
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
            record = store.add_item(args.sku, args.quantity)
        elif args.command == "reserve":
            record = store.reserve(args.key, args.sku, args.quantity)
        elif args.command == "release":
            record = store.release(args.id)
        else:
            record = store.report()
    except (Conflict, NotFound, ValueError, OSError) as exc:
        print(json.dumps({"error": str(exc)}), file=sys.stderr)
        return 2
    print(json.dumps(record, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
