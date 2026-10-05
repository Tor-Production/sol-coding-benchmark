"""Command-line interface for ``python -m reservation``."""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Sequence
from typing import Any

from .http_api import create_server
from .store import Conflict, NotFound, Store


class _UsageError(Exception):
    pass


class _ArgumentParser(argparse.ArgumentParser):
    def error(self, message: str) -> None:
        raise _UsageError(message)


def _port(value: str) -> int:
    try:
        port = int(value, 10)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("port must be an integer") from exc
    if not 0 <= port <= 65535:
        raise argparse.ArgumentTypeError("port must be between 0 and 65535")
    return port


def _parser() -> argparse.ArgumentParser:
    parser = _ArgumentParser(prog="python -m reservation")
    parser.add_argument("--db", required=True, help="path to the SQLite database")
    commands = parser.add_subparsers(dest="command", required=True)

    add = commands.add_parser("add", help="add stock for an item")
    add.add_argument("--sku", required=True)
    add.add_argument("--quantity", required=True, type=int)

    reserve = commands.add_parser("reserve", help="reserve stock")
    reserve.add_argument("--key", required=True)
    reserve.add_argument("--sku", required=True)
    reserve.add_argument("--quantity", required=True, type=int)

    release = commands.add_parser("release", help="release a reservation")
    release.add_argument("--id", required=True, type=int)

    commands.add_parser("report", help="show inventory and active reservations")

    serve = commands.add_parser("serve", help="serve the HTTP API")
    serve.add_argument("--host", default="127.0.0.1")
    serve.add_argument("--port", default=0, type=_port)
    return parser


def _write_json(stream: Any, value: dict[str, Any]) -> None:
    stream.write(json.dumps(value, ensure_ascii=False, separators=(",", ":")))
    stream.write("\n")
    stream.flush()


def main(argv: Sequence[str] | None = None) -> int:
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
    except (_UsageError, Conflict, NotFound, ValueError) as exc:
        _write_json(sys.stderr, {"error": str(exc)})
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
