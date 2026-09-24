"""Adapter for Merchant Profit OS's published catalog and order CSV contracts."""

from __future__ import annotations

import csv
from collections import defaultdict
from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path

from .core import DataError, HEADERS, _int, _money, _table, _unique


MPOS_HEADERS = {
    "catalog": ("sku", "title", "description", "brand", "product_url", "image_url", "price_usd", "unit_cost_usd", "fulfillment_cost_usd", "inventory_quantity", "availability", "updated_at"),
    "orders": ("order_id", "date", "market", "sku", "quantity", "unit_price_usd", "discount_usd", "shipping_revenue_usd", "refund_usd", "shipping_cost_usd"),
}


def _read(path: Path, name: str) -> list[dict[str, str]]:
    try:
        with path.open(newline="", encoding="utf-8-sig") as handle:
            reader = csv.DictReader(handle)
            if tuple(reader.fieldnames or ()) != MPOS_HEADERS[name]:
                raise DataError(f"Merchant Profit OS {name}.csv has an unexpected schema")
            rows = list(reader)
    except (OSError, UnicodeError, csv.Error) as exc:
        raise DataError(f"Could not read Merchant Profit OS {name}.csv") from exc
    if any(None in row or any(value is None for value in row.values()) for row in rows):
        raise DataError(f"Merchant Profit OS {name}.csv has malformed rows")
    return rows


def derive_mpos_economics(mpos_folder: Path, mapping_path: Path, output: Path, *, as_of_date: str,
                          market: str, attest_complete_orders: bool) -> dict:
    if not attest_complete_orders:
        raise DataError("explicit 30-day order completeness attestation is required")
    try:
        end = date.fromisoformat(as_of_date)
        if end.isoformat() != as_of_date:
            raise ValueError
    except ValueError as exc:
        raise DataError("as_of_date must be YYYY-MM-DD") from exc
    if not market:
        raise DataError("market is required")
    lower = end - timedelta(days=29)
    mapping_folder = Path(mapping_path).parent
    if Path(mapping_path).name != "mapping.csv":
        raise DataError("mapping path must name mapping.csv")
    mapping, _ = _table(mapping_folder, "mapping")
    wanted = _unique(mapping, "sku", "mapping")
    catalog = _unique(_read(Path(mpos_folder) / "catalog.csv", "catalog"), "sku", "catalog")
    orders = _read(Path(mpos_folder) / "orders.csv", "orders")
    units = defaultdict(int)
    seen_orders = set()
    for row in orders:
        if not row["order_id"] or row["order_id"] in seen_orders:
            raise DataError("Merchant Profit OS order IDs must be unique")
        seen_orders.add(row["order_id"])
        try:
            day = date.fromisoformat(row["date"])
        except ValueError as exc:
            raise DataError("Merchant Profit OS order has invalid date") from exc
        if row["market"] == market and lower <= day <= end and row["sku"] in wanted:
            units[row["sku"]] += _int(row["quantity"], "order quantity")
    rows = []
    for sku in wanted:
        if sku not in catalog:
            continue
        item = catalog[sku]
        price = _money(item["price_usd"], f"catalog:{sku}:price")
        costs = _money(item["unit_cost_usd"], f"catalog:{sku}:unit_cost") + _money(item["fulfillment_cost_usd"], f"catalog:{sku}:fulfillment")
        if costs > price:
            raise DataError(f"catalog:{sku} has negative modeled unit margin; cannot use nonnegative priority proxy")
        rows.append({"sku": sku, "unit_margin_usd": f"{price - costs:.2f}", "units_sold_30d": str(units[sku])})
    output = Path(output)
    if output.exists():
        raise DataError("economics output already exists; do not overwrite customer data")
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=HEADERS["economics"])
        writer.writeheader()
        writer.writerows(rows)
    return {"rows": len(rows), "market": market, "window_start": lower.isoformat(), "window_end": end.isoformat(),
            "claim_limit": "Gross units and current catalog costs; not realized contribution or recovered revenue"}
