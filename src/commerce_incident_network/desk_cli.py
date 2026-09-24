"""Local command-center pilot CLI."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .core import DataError
from .desk import render_desk, sync, transition, view


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Local, single-merchant operator desk")
    parser.add_argument("database", type=Path, help="Private SQLite file for one merchant/channel scope")
    sub = parser.add_subparsers(dest="command", required=True)
    capture = sub.add_parser("sync", help="Import a fresh complete snapshot into the desk")
    capture.add_argument("inputs", type=Path)
    capture.add_argument("--as-of", required=True)
    capture.add_argument("--actor", required=True)
    list_cmd = sub.add_parser("list", help="Show cases and event trail as JSON")
    list_cmd.add_argument("--active-only", action="store_true")
    assign = sub.add_parser("assign", help="Assign a case to a human owner")
    assign.add_argument("case_id")
    assign.add_argument("--owner", required=True)
    assign.add_argument("--actor", required=True)
    approve = sub.add_parser("approve", help="Approve a proposed manual fix")
    approve.add_argument("case_id")
    approve.add_argument("--actor", required=True)
    approve.add_argument("--note", required=True)
    applied = sub.add_parser("record-fix", help="Owner declares manual fix completed elsewhere")
    applied.add_argument("case_id")
    applied.add_argument("--actor", required=True)
    applied.add_argument("--note", required=True)
    dashboard = sub.add_parser("dashboard", help="Export a static, offline operator dashboard")
    dashboard.add_argument("output", type=Path)
    args = parser.parse_args(argv)
    try:
        if args.command == "sync":
            result = sync(args.database, args.inputs, args.as_of, args.actor)
        elif args.command == "list":
            result = view(args.database)
            if args.active_only:
                result["cases"] = [case for case in result["cases"] if case["state"] != "resolved_observed"]
        elif args.command == "dashboard":
            args.output.parent.mkdir(parents=True, exist_ok=True)
            args.output.write_text(render_desk(view(args.database)), encoding="utf-8")
            result = {"dashboard": str(args.output)}
        else:
            action = {"assign": "assign", "approve": "approve_manual_fix", "record-fix": "record_manual_fix"}[args.command]
            result = transition(args.database, args.case_id, action, args.actor,
                                note=getattr(args, "note", ""), owner=getattr(args, "owner", ""))
        print(json.dumps(result, indent=2, sort_keys=True))
        return 0
    except (DataError, OSError, ValueError, KeyError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
