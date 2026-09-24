"""Strict, deterministic comparison of complete merchant snapshots."""

from __future__ import annotations

import csv
import hashlib
import json
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path


SCHEMA_VERSION = "1.1"
HEADERS = {
    "mapping": ("sku", "offer_id"),
    "storefront": ("sku", "price_usd", "inventory_quantity", "published"),
    "channel": ("offer_id", "price_usd", "availability", "approval_status"),
    "economics": ("sku", "unit_margin_usd", "units_sold_30d"),
}
KINDS = {
    "channel_missing": ("high", "Check the channel feed and product eligibility."),
    "channel_disapproved": ("high", "Review channel issue details and correct the source data."),
    "channel_pending": ("medium", "Check whether approval is still processing."),
    "price_mismatch": ("medium", "Review the storefront and channel price before changing either."),
    "availability_mismatch": ("high", "Review inventory and channel availability before changing either."),
    "storefront_unpublished_channel_live": ("high", "Review storefront publication and channel listing."),
    "storefront_missing": ("high", "Review the product mapping and storefront export."),
}


class DataError(ValueError):
    """Input cannot safely support an incident comparison."""


def _datetime(value: str, label: str) -> datetime:
    try:
        result = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (ValueError, AttributeError) as exc:
        raise DataError(f"{label} must be an ISO-8601 timestamp with timezone") from exc
    if result.tzinfo is None:
        raise DataError(f"{label} must include a timezone")
    return result.astimezone(timezone.utc)


def _money(value: str, label: str) -> Decimal:
    try:
        number = Decimal(value)
    except (InvalidOperation, TypeError) as exc:
        raise DataError(f"{label} must be a decimal number") from exc
    if not number.is_finite() or number < 0 or number.as_tuple().exponent < -2:
        raise DataError(f"{label} must be nonnegative USD with at most two decimal places")
    return number


def _int(value: str, label: str) -> int:
    if not value.isdigit():
        raise DataError(f"{label} must be a nonnegative integer")
    return int(value)


def _table(folder: Path, name: str) -> tuple[list[dict[str, str]], str]:
    path = folder / f"{name}.csv"
    try:
        raw = path.read_bytes()
        with path.open("r", encoding="utf-8-sig", newline="") as handle:
            reader = csv.DictReader(handle)
            if tuple(reader.fieldnames or ()) != HEADERS[name]:
                raise DataError(f"{name}.csv needs exact columns: {', '.join(HEADERS[name])}")
            rows = list(reader)
    except (OSError, UnicodeError) as exc:
        raise DataError(f"Cannot read {path}: {exc}") from exc
    if not rows and name == "mapping":
        raise DataError("mapping.csv needs at least one mapped product")
    for index, row in enumerate(rows, 2):
        if None in row or any(value is None for value in row.values()):
            raise DataError(f"{name}.csv row {index} has the wrong number of columns")
    return rows, hashlib.sha256(raw).hexdigest()


def _unique(rows: list[dict[str, str]], key: str, label: str) -> dict[str, dict[str, str]]:
    result = {}
    for row in rows:
        value = row[key].strip()
        if not value or value in result:
            raise DataError(f"{label} has an empty or duplicate {key}: {value!r}")
        result[value] = row
    return result


def _incident_id(merchant: str, shop_domain: str, google_account_id: str, country: str, context: str, language: str, feed_label: str, sku: str, kind: str) -> str:
    payload = json.dumps([merchant, shop_domain, google_account_id, country, context, language, feed_label, sku, kind], separators=(",", ":"))
    return hashlib.sha256(payload.encode()).hexdigest()[:20]


def build_report(folder: Path, as_of: str, previous: dict | None = None, max_age_hours: int = 24) -> dict:
    folder = Path(folder)
    try:
        manifest_raw = (folder / "manifest.json").read_bytes()
        manifest = json.loads(manifest_raw)
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise DataError("manifest.json must be readable JSON") from exc
    required = {"merchant_id", "shop_domain", "google_account_id", "country", "reporting_context", "content_language", "feed_label", "currency", "snapshot_at", "storefront_complete", "channel_complete"}
    if set(manifest) != required:
        raise DataError(f"manifest.json needs exact fields: {', '.join(sorted(required))}")
    for key in ("merchant_id", "shop_domain", "google_account_id", "country", "reporting_context", "content_language", "feed_label"):
        if not isinstance(manifest[key], str) or not manifest[key].strip():
            raise DataError(f"{key} is required")
    if manifest["currency"] != "USD":
        raise DataError("v1 supports USD only; do not compare converted prices silently")
    if manifest["storefront_complete"] is not True or manifest["channel_complete"] is not True:
        raise DataError("Both snapshot_complete flags must be true")
    stamp = _datetime(manifest["snapshot_at"], "snapshot_at")
    now = _datetime(as_of, "as_of")
    if max_age_hours <= 0 or stamp > now or (now - stamp).total_seconds() > max_age_hours * 3600:
        raise DataError("snapshot is future-dated or stale; resolution cannot be inferred")

    tables = {}
    hashes = {"manifest.json": hashlib.sha256(manifest_raw).hexdigest()}
    for name in HEADERS:
        tables[name], hashes[f"{name}.csv"] = _table(folder, name)
    mapping = _unique(tables["mapping"], "sku", "mapping")
    _unique(tables["mapping"], "offer_id", "mapping")
    storefront = _unique(tables["storefront"], "sku", "storefront")
    channel = _unique(tables["channel"], "offer_id", "channel")
    economics = _unique(tables["economics"], "sku", "economics")
    if set(storefront) - set(mapping) or set(channel) - {r["offer_id"] for r in mapping.values()} or set(economics) - set(mapping):
        raise DataError("Every source row must have an explicit mapping; no silent SKU joins")
    for sku, row in storefront.items():
        _money(row["price_usd"], f"storefront:{sku}:price_usd")
        _int(row["inventory_quantity"], f"storefront:{sku}:inventory_quantity")
        if row["published"] not in {"true", "false"}:
            raise DataError(f"storefront:{sku}:published must be true or false")
    for offer, row in channel.items():
        _money(row["price_usd"], f"channel:{offer}:price_usd")
        if row["availability"] not in {"in_stock", "out_of_stock"} or row["approval_status"] not in {"approved", "pending", "disapproved"}:
            raise DataError(f"channel:{offer} has invalid availability or approval_status")
    for sku, row in economics.items():
        _money(row["unit_margin_usd"], f"economics:{sku}:unit_margin_usd")
        _int(row["units_sold_30d"], f"economics:{sku}:units_sold_30d")

    if previous:
        for key in ("merchant_id", "shop_domain", "google_account_id", "country", "reporting_context", "content_language", "feed_label", "currency"):
            if previous.get("scope", {}).get(key) != manifest[key]:
                raise DataError(f"previous report scope differs on {key}")
        if previous.get("schema_version") != SCHEMA_VERSION or _datetime(previous["snapshot_at"], "previous snapshot_at") >= stamp:
            raise DataError("previous report must be same schema and older than current snapshot")

    incidents = []
    old_active = {row["id"]: row for row in (previous or {}).get("incidents", []) if row["state"] != "resolved_observed"}

    def add(sku: str, offer_id: str, kind: str, detail: str) -> None:
        identifier = _incident_id(manifest["merchant_id"], manifest["shop_domain"], manifest["google_account_id"], manifest["country"], manifest["reporting_context"], manifest["content_language"], manifest["feed_label"], sku, kind)
        econ = economics.get(sku)
        estimate = None
        if econ:
            daily = _money(econ["unit_margin_usd"], "unit_margin_usd") * Decimal(_int(econ["units_sold_30d"], "units_sold_30d")) / Decimal(30)
            estimate = str(daily.quantize(Decimal("0.01")))
        severity, recommendation = KINDS[kind]
        incidents.append({"id": identifier, "sku": sku, "offer_id": offer_id, "kind": kind,
                          "severity": severity, "state": "ongoing" if identifier in old_active else "new",
                          "detail": detail, "recommendation": recommendation,
                          "daily_margin_priority_proxy_usd": estimate})

    for sku, relation in mapping.items():
        offer = relation["offer_id"]
        shop = storefront.get(sku)
        dest = channel.get(offer)
        if shop is None:
            add(sku, offer, "storefront_missing", "Mapped SKU absent from complete storefront snapshot")
            continue
        if dest is None:
            if shop["published"] == "true":
                add(sku, offer, "channel_missing", "Published SKU absent from complete channel snapshot")
            continue
        if dest["approval_status"] == "disapproved":
            add(sku, offer, "channel_disapproved", "Channel reports disapproved")
        elif dest["approval_status"] == "pending":
            add(sku, offer, "channel_pending", "Channel approval still pending")
        if _money(shop["price_usd"], "storefront price") != _money(dest["price_usd"], "channel price"):
            add(sku, offer, "price_mismatch", f"Storefront USD {shop['price_usd']}; channel USD {dest['price_usd']}")
        if shop["published"] == "false":
            if dest["approval_status"] == "approved" and dest["availability"] == "in_stock":
                add(sku, offer, "storefront_unpublished_channel_live", "Unpublished storefront product still live in channel")
        elif (int(shop["inventory_quantity"]) > 0) != (dest["availability"] == "in_stock"):
            add(sku, offer, "availability_mismatch", f"Storefront quantity {shop['inventory_quantity']}; channel {dest['availability']}")

    active_ids = {row["id"] for row in incidents}
    for identifier, old in old_active.items():
        if identifier not in active_ids:
            incidents.append({**old, "state": "resolved_observed", "detail": "Absent in newer complete snapshots; operator should confirm root cause"})
    incidents.sort(key=lambda row: (row["state"] == "resolved_observed", row["severity"] != "high",
                                    -(Decimal(row["daily_margin_priority_proxy_usd"]) if row["daily_margin_priority_proxy_usd"] else Decimal(-1)), row["sku"], row["kind"]))
    return {"schema_version": SCHEMA_VERSION, "scope": {key: manifest[key] for key in ("merchant_id", "shop_domain", "google_account_id", "country", "reporting_context", "content_language", "feed_label", "currency")},
            "snapshot_at": manifest["snapshot_at"], "as_of": as_of,
            "source_sha256": hashes, "counts": {key: len(value) for key, value in tables.items()},
            "incidents": incidents, "summary": {"active": sum(i["state"] != "resolved_observed" for i in incidents),
            "new": sum(i["state"] == "new" for i in incidents), "ongoing": sum(i["state"] == "ongoing" for i in incidents),
            "resolved_observed": sum(i["state"] == "resolved_observed" for i in incidents)},
            "claim_limits": "Daily margin priority is historical velocity times modeled unit margin; it is not lost or recovered revenue. Resolved means absent in a newer complete snapshot, not verified root cause or incremental sales."}
