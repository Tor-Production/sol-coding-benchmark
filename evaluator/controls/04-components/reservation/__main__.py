import argparse
import json
import sys
from .store import Store, Conflict, NotFound
from .http_api import create_server


class Parser(argparse.ArgumentParser):
    def error(self, message):
        print(json.dumps({"error": message}), file=sys.stderr)
        raise SystemExit(2)


def main():
    parser = Parser()
    parser.add_argument("--db", required=True)
    commands = parser.add_subparsers(dest="command", required=True, parser_class=Parser)
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
    args = parser.parse_args()
    try:
        if args.command == "serve":
            server = create_server(args.db, args.host, args.port)
            try:
                server.serve_forever()
            except KeyboardInterrupt:
                pass
            finally:
                server.server_close()
            return
        store = Store(args.db)
        if args.command == "add":
            record = store.add_item(args.sku, args.quantity)
        elif args.command == "reserve":
            record = store.reserve(args.key, args.sku, args.quantity)
        elif args.command == "release":
            record = store.release(args.id)
        else:
            record = store.report()
        print(json.dumps(record))
    except (ValueError, Conflict, NotFound) as exc:
        print(json.dumps({"error": str(exc)}), file=sys.stderr)
        raise SystemExit(2)


if __name__ == "__main__":
    main()
