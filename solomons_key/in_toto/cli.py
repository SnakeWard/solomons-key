"""Thin CLI for the optional in-toto exporter and consumer.

    python -m solomons_key.in_toto emit <run-dir> [-o FILE]
    python -m solomons_key.in_toto consume <statement.json>
"""

from __future__ import annotations

import argparse
import json
import sys

from .consume import consume_path
from .export import emit_statement, write_statement


def cmd_emit(args: argparse.Namespace) -> int:
    try:
        statement = emit_statement(args.run_dir)
    except FileNotFoundError as exc:
        print(f"sk-intoto emit: {exc}", file=sys.stderr)
        return 3
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print(f"sk-intoto emit: unreadable run: {exc}", file=sys.stderr)
        return 3
    if args.out:
        write_statement(statement, args.out)
        print(f"sk-intoto: wrote {args.out}")
    else:
        json.dump(statement, sys.stdout, indent=2, sort_keys=True)
        sys.stdout.write("\n")
    return 0


def cmd_consume(args: argparse.Namespace) -> int:
    result = consume_path(args.statement)
    if result.ok:
        print(f"sk-intoto consume: valid {args.statement}")
        return 0
    print(f"sk-intoto consume: REJECT {args.statement}", file=sys.stderr)
    for error in result.errors:
        print(f"  {error}", file=sys.stderr)
    if any(item.startswith("unreadable:") for item in result.errors):
        return 3
    return 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="solomons_key.in_toto",
        description=(
            "Export or structurally validate a Solomon's Key in-toto statement. "
            "Not an SLSA builder. Trust root remains TRUSTED_PROGRAMS.sha256."
        ),
    )
    sub = parser.add_subparsers(dest="cmd", required=True)

    emit = sub.add_parser("emit", help="export a statement from an existing Key run")
    emit.add_argument("run_dir", help="path to a Key run directory (contains run.json)")
    emit.add_argument("-o", "--out", help="write JSON here instead of stdout")
    emit.set_defaults(fn=cmd_emit)

    consume = sub.add_parser("consume", help="structurally validate a statement")
    consume.add_argument("statement", help="path to a statement JSON file")
    consume.set_defaults(fn=cmd_consume)

    args = parser.parse_args(argv)
    return args.fn(args)
