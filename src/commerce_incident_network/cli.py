"""Command line and self-contained local review screen."""

from __future__ import annotations

import argparse
import csv
import html
import json
import os
import sys
from pathlib import Path

from .core import DataError, build_report
from .capture import capture_snapshot
from .economics import derive_mpos_economics


def _cell(value: object) -> str:
    return html.escape(str(value if value is not None else "—"), quote=True)


def _csv_safe(value: object) -> str:
    text = "" if value is None else str(value)
    return "'" + text if text.lstrip().startswith(("=", "+", "-", "@", "\t", "\r")) else text


def render_html(report: dict) -> str:
    rows = "".join(
        "<tr>" + "".join(f"<td>{_cell(incident.get(field))}</td>" for field in
        ("state", "severity", "sku", "kind", "detail", "daily_margin_priority_proxy_usd", "recommendation")) + "</tr>"
        for incident in report["incidents"]
    )
    summary = report["summary"]
    return f"""<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>Commerce Incident Network</title><style>
:root{{color-scheme:dark}}body{{font:16px/1.5 system-ui,sans-serif;background:#0d1723;color:#e9f0f8;margin:0}}
main{{max-width:1400px;margin:auto;padding:32px 24px}}h1{{font-size:2rem;margin-bottom:4px}}h2{{margin-top:36px}}
.sub{{color:#bad0e5}}.cards{{display:flex;gap:12px;flex-wrap:wrap;margin:24px 0}}.card{{background:#1b2c40;border:1px solid #486884;border-radius:12px;padding:16px;min-width:155px}}
.card strong{{display:block;font-size:1.8rem;color:#fff}}.scroll{{overflow-x:auto}}table{{border-collapse:collapse;width:100%;background:#15263a}}
th,td{{border:1px solid #38546f;padding:10px;text-align:left;vertical-align:top}}th{{background:#29435f;color:#fff}}
.note{{border-left:4px solid #77b7df;background:#1b3044;padding:14px}}code{{color:#9ddcff}}
</style></head><body><main><h1>Commerce Incident Network</h1>
<p class="sub">{_cell(report['scope']['merchant_id'])} · {_cell(report['scope']['country'])} · {_cell(report['scope']['reporting_context'])} · snapshot {_cell(report['snapshot_at'])}</p>
<div class="cards"><div class="card"><strong>{summary['active']}</strong>active</div><div class="card"><strong>{summary['new']}</strong>new</div><div class="card"><strong>{summary['ongoing']}</strong>ongoing</div><div class="card"><strong>{summary['resolved_observed']}</strong>resolved observed</div></div>
<p class="note">{_cell(report['claim_limits'])}</p><h2>Incident queue</h2><div class="scroll"><table><thead><tr><th>State</th><th>Severity</th><th>SKU</th><th>Kind</th><th>Evidence</th><th>Daily margin priority proxy, USD</th><th>Operator next step</th></tr></thead><tbody>{rows}</tbody></table></div>
<p class="sub">Read-only local analysis. No credentials, scripts, telemetry, price changes, or feed writes.</p></main></body></html>"""


def write_outputs(report: dict, output: Path) -> None:
    output.mkdir(parents=True, exist_ok=True)
    (output / "report.json").write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    (output / "dashboard.html").write_text(render_html(report), encoding="utf-8")
    with (output / "incidents.csv").open("w", newline="", encoding="utf-8") as handle:
        fields = ("id", "state", "severity", "sku", "offer_id", "kind", "detail", "daily_margin_priority_proxy_usd", "recommendation")
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        for incident in report["incidents"]:
            writer.writerow({field: _csv_safe(incident.get(field)) for field in fields})


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Compare complete storefront and channel snapshots")
    sub = parser.add_subparsers(dest="command", required=True)
    run = sub.add_parser("run", help="Generate incident queue and local dashboard")
    run.add_argument("inputs", type=Path)
    run.add_argument("output", type=Path)
    run.add_argument("--as-of", required=True, help="ISO-8601 timestamp with timezone")
    run.add_argument("--previous", type=Path, help="Earlier report.json for lifecycle comparison")
    run.add_argument("--max-age-hours", type=int, default=24)
    verify = sub.add_parser("verify", help="Recompute a report from its source snapshots")
    verify.add_argument("inputs", type=Path)
    verify.add_argument("report", type=Path)
    verify.add_argument("--previous", type=Path)
    verify.add_argument("--max-age-hours", type=int, default=24)
    capture = sub.add_parser("capture", help="Read Shopify and Merchant API into one complete local snapshot")
    capture.add_argument("template", type=Path, help="Directory with mapping.csv and economics.csv")
    capture.add_argument("output", type=Path, help="New immutable snapshot directory")
    capture.add_argument("--shop-domain", required=True)
    capture.add_argument("--google-account", required=True)
    capture.add_argument("--merchant-id", required=True)
    capture.add_argument("--country", required=True)
    capture.add_argument("--context", default="SHOPPING_ADS")
    capture.add_argument("--language", default="en")
    capture.add_argument("--feed-label", required=True)
    economics = sub.add_parser("economics-from-mpos", help="Derive optional priority inputs from Merchant Profit OS CSVs")
    economics.add_argument("mpos_inputs", type=Path)
    economics.add_argument("mapping", type=Path)
    economics.add_argument("output", type=Path)
    economics.add_argument("--as-of-date", required=True)
    economics.add_argument("--market", required=True)
    economics.add_argument("--attest-complete-orders", action="store_true")
    args = parser.parse_args(argv)
    try:
        if args.command == "economics-from-mpos":
            result = derive_mpos_economics(args.mpos_inputs, args.mapping, args.output,
                as_of_date=args.as_of_date, market=args.market,
                attest_complete_orders=args.attest_complete_orders)
            print(json.dumps(result, sort_keys=True))
            return 0
        if args.command == "capture":
            result = capture_snapshot(args.template, args.output, shop_domain=args.shop_domain,
                                      account_id=args.google_account, merchant_id=args.merchant_id,
                                      country=args.country, context=args.context, language=args.language,
                                      feed_label=args.feed_label,
                                      shop_token=os.environ.get("SHOPIFY_ADMIN_TOKEN", ""),
                                      google_token=os.environ.get("GOOGLE_MERCHANT_ACCESS_TOKEN", ""))
            print(json.dumps(result, sort_keys=True))
            return 0
        previous = json.loads(args.previous.read_text(encoding="utf-8")) if args.previous else None
        if args.command == "run":
            report = build_report(args.inputs, args.as_of, previous, args.max_age_hours)
            write_outputs(report, args.output)
            print(f"Wrote {args.output / 'report.json'} with {report['summary']['active']} active incidents")
        else:
            saved = json.loads(args.report.read_text(encoding="utf-8"))
            recomputed = build_report(args.inputs, saved["as_of"], previous, args.max_age_hours)
            if saved != recomputed:
                raise DataError("report differs from recomputed source snapshots")
            print("Verified report against current source files")
        return 0
    except (DataError, OSError, ValueError, KeyError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
