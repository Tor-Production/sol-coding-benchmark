"""CLI entry point."""
import argparse
import json
import sys
from .store import Store, Conflict, NotFound
from .http_api import create_server

def integer(value):
    try:
        return int(value)
    except ValueError:
        raise argparse.ArgumentTypeError("expected integer") from None

class JsonParser(argparse.ArgumentParser):
    def error(self, message):
        print(json.dumps({"error": message}), file=sys.stderr)
        raise SystemExit(2)

def main():
    parser = JsonParser()
    parser.add_argument("--db", required=True)
    commands = parser.add_subparsers(dest="command", required=True)
    add = commands.add_parser("add")
    add.add_argument("--sku", required=True)
    add.add_argument("--quantity", type=integer, required=True)
    reserve = commands.add_parser("reserve")
    reserve.add_argument("--key", required=True)
    reserve.add_argument("--sku", required=True)
    reserve.add_argument("--quantity", type=integer, required=True)
    release = commands.add_parser("release")
    release.add_argument("--id", type=integer, required=True)
    commands.add_parser("report")
    serve = commands.add_parser("serve")
    serve.add_argument("--host", default="127.0.0.1")
    serve.add_argument("--port", type=integer, default=0)
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
        print(json.dumps({"error": str(exc)}), file=sys.stderr)
        return 2

if __name__ == "__main__":
    sys.exit(main())
