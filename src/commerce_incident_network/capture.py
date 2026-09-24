"""Permissioned, read-only snapshot capture from Shopify and Google Merchant API.

No connector writes to either platform. A snapshot is published only after every
API page and every mapped product can be normalized without ambiguity.
"""

from __future__ import annotations

import csv
import json
import os
import re
import tempfile
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from .core import DataError, HEADERS, _money, _table, _unique, build_report


SHOPIFY_QUERY = """query CaptureVariants($cursor: String) {
  shop { currencyCode }
  productVariants(first: 100, after: $cursor) {
    edges { node {
      sku price inventoryQuantity inventoryPolicy inventoryItem { tracked }
      product { status publishedAt }
    } }
    pageInfo { hasNextPage endCursor }
  }
}"""
SHOP_RE = re.compile(r"^[a-z0-9][a-z0-9-]*\.myshopify\.com$")
ACCOUNT_RE = re.compile(r"^[0-9]+$")


def request_json(url: str, token: str, payload: dict | None = None) -> dict:
    headers = {"Accept": "application/json"}
    if payload is None:
        headers["Authorization"] = f"Bearer {token}"
        body = None
    else:
        headers["X-Shopify-Access-Token"] = token
        headers["Content-Type"] = "application/json"
        body = json.dumps(payload).encode("utf-8")
    try:
        with urlopen(Request(url, data=body, headers=headers), timeout=30) as response:
            result = json.load(response)
    except HTTPError as exc:
        raise DataError(f"Platform API returned HTTP {exc.code}; snapshot was not written") from exc
    except (URLError, TimeoutError, json.JSONDecodeError) as exc:
        raise DataError("Platform API request failed; snapshot was not written") from exc
    if not isinstance(result, dict):
        raise DataError("Platform API returned an invalid JSON object")
    return result


def fetch_shopify(shop_domain: str, token: str, wanted_skus: set[str], request=request_json) -> dict[str, dict[str, str]]:
    if not SHOP_RE.fullmatch(shop_domain):
        raise DataError("Shopify domain must be a validated *.myshopify.com hostname")
    if not token:
        raise DataError("SHOPIFY_ADMIN_TOKEN is required")
    url = f"https://{shop_domain}/admin/api/2026-07/graphql.json"
    cursor = None
    seen_cursors = set()
    found = {}
    for _ in range(1000):
        data = request(url, token, {"query": SHOPIFY_QUERY, "variables": {"cursor": cursor}})
        if data.get("errors"):
            raise DataError("Shopify GraphQL returned errors; snapshot was not written")
        try:
            root = data["data"]
            if root["shop"]["currencyCode"] != "USD":
                raise DataError("Shopify shop currency is not USD")
            connection = root["productVariants"]
            edges = connection["edges"]
            page = connection["pageInfo"]
        except (KeyError, TypeError) as exc:
            raise DataError("Shopify response is missing expected fields") from exc
        if not isinstance(edges, list):
            raise DataError("Shopify variant edges must be a list")
        if not isinstance(page.get("hasNextPage"), bool):
            raise DataError("Shopify pagination state is missing")
        for edge in edges:
            try:
                node = edge["node"]
                sku = node["sku"]
            except (KeyError, TypeError) as exc:
                raise DataError("Shopify variant is missing SKU") from exc
            if not sku or sku not in wanted_skus:
                continue
            if sku in found:
                raise DataError(f"Shopify returned duplicate mapped SKU {sku!r}")
            try:
                product = node["product"]
                price = str(node["price"])
                quantity = node["inventoryQuantity"]
                policy = node["inventoryPolicy"]
                tracked = node["inventoryItem"]["tracked"]
                status = product["status"]
                published_at = product["publishedAt"]
            except (KeyError, TypeError) as exc:
                raise DataError(f"Shopify mapped SKU {sku!r} lacks required fields") from exc
            _money(price, f"Shopify:{sku}:price")
            if not isinstance(quantity, int) or quantity < 0 or policy != "DENY" or tracked is not True:
                raise DataError(f"Shopify mapped SKU {sku!r} has unsupported inventory semantics")
            if status not in {"ACTIVE", "ARCHIVED", "DRAFT", "UNLISTED"}:
                raise DataError(f"Shopify mapped SKU {sku!r} has unknown product status")
            found[sku] = {"sku": sku, "price_usd": price, "inventory_quantity": str(quantity),
                          "published": "true" if status == "ACTIVE" and published_at else "false"}
        if page.get("hasNextPage") is not True:
            return found
        next_cursor = page.get("endCursor")
        if not next_cursor or next_cursor in seen_cursors:
            raise DataError("Shopify pagination cursor is missing or repeated")
        seen_cursors.add(next_cursor)
        cursor = next_cursor
    raise DataError("Shopify exceeded 1000 pages; no complete snapshot was written")


def fetch_google(account_id: str, token: str, wanted_offers: set[str], country: str,
                 context: str, language: str, feed_label: str, request=request_json) -> dict[str, dict[str, str]]:
    if not ACCOUNT_RE.fullmatch(account_id):
        raise DataError("Merchant account ID must be numeric")
    if not token:
        raise DataError("GOOGLE_MERCHANT_ACCESS_TOKEN is required")
    base = f"https://merchantapi.googleapis.com/products/v1/accounts/{account_id}/products"
    page_token = None
    seen_tokens = set()
    found = {}
    for _ in range(1000):
        query = {"pageSize": "1000"}
        if page_token:
            query["pageToken"] = page_token
        data = request(base + "?" + urlencode(query), token)
        if "error" in data:
            raise DataError("Google API returned an error")
        products = data.get("products", [])
        if not isinstance(products, list):
            raise DataError("Google products must be a list")
        for product in products:
            if not isinstance(product, dict) or product.get("contentLanguage") != language or product.get("feedLabel") != feed_label:
                continue
            offer = product.get("offerId")
            if offer not in wanted_offers:
                continue
            if offer in found:
                raise DataError(f"Google returned duplicate mapped offer {offer!r}")
            try:
                attrs = product["productAttributes"]
                raw_price = attrs["price"]
                if raw_price["currencyCode"] != "USD":
                    raise DataError(f"Google offer {offer!r} currency is not USD")
                price = Decimal(str(raw_price["amountMicros"])) / Decimal(1_000_000)
                _money(str(price), f"Google:{offer}:price")
                if attrs.get("salePrice"):
                    raise DataError(f"Google offer {offer!r} has salePrice; v1 cannot compare active promotions")
                availability = attrs["availability"]
                if availability not in {"IN_STOCK", "OUT_OF_STOCK"}:
                    raise DataError(f"Google offer {offer!r} has unsupported availability {availability!r}")
                statuses = [row for row in product["productStatus"]["destinationStatuses"] if row["reportingContext"] == context]
                if len(statuses) != 1:
                    raise DataError(f"Google offer {offer!r} has ambiguous status for {context}")
                status = statuses[0]
                matches = [label for label, field in (("approved", "approvedCountries"), ("pending", "pendingCountries"),
                           ("disapproved", "disapprovedCountries")) if country in status.get(field, [])]
                if len(matches) != 1:
                    raise DataError(f"Google offer {offer!r} has ambiguous approval for {country}")
            except (KeyError, TypeError, ValueError, InvalidOperation) as exc:
                if isinstance(exc, DataError):
                    raise
                raise DataError(f"Google offer {offer!r} is missing required processed-product fields") from exc
            found[offer] = {"offer_id": offer, "price_usd": f"{price:.2f}",
                            "availability": availability.lower(), "approval_status": matches[0]}
        next_token = data.get("nextPageToken")
        if not next_token:
            return found
        if next_token in seen_tokens:
            raise DataError("Google pagination token repeated")
        seen_tokens.add(next_token)
        page_token = next_token
    raise DataError("Google exceeded 1000 pages; no complete snapshot was written")


def capture_snapshot(template: Path, output: Path, *, shop_domain: str, account_id: str, merchant_id: str,
                     country: str, context: str, language: str, feed_label: str,
                     shop_token: str, google_token: str, now: datetime | None = None,
                     shop_request=request_json, google_request=request_json) -> dict:
    """Capture mapped products; publish directory only after complete pagination and validation."""
    template, output = Path(template), Path(output)
    if output.exists():
        raise DataError("Output snapshot directory already exists; snapshots are immutable")
    mapping_rows, _ = _table(template, "mapping")
    economics_rows, _ = _table(template, "economics")
    mapping = _unique(mapping_rows, "sku", "mapping")
    _unique(mapping_rows, "offer_id", "mapping")
    _unique(economics_rows, "sku", "economics")
    if not merchant_id or not country or not context or not language or not feed_label:
        raise DataError("Merchant and channel scope fields are required")
    shop_rows = fetch_shopify(shop_domain, shop_token, set(mapping), request=shop_request)
    google_rows = fetch_google(account_id, google_token, {row["offer_id"] for row in mapping_rows},
                               country, context, language, feed_label, request=google_request)
    stamp = (now or datetime.now(timezone.utc)).astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
    manifest = {"merchant_id": merchant_id, "shop_domain": shop_domain, "google_account_id": account_id,
                "country": country, "reporting_context": context,
                "content_language": language, "feed_label": feed_label, "currency": "USD",
                "snapshot_at": stamp, "storefront_complete": True, "channel_complete": True}
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="capture-", dir=output.parent) as temp:
        folder = Path(temp)
        for name, rows in (("mapping", mapping_rows), ("economics", economics_rows),
                           ("storefront", list(shop_rows.values())), ("channel", list(google_rows.values()))):
            with (folder / f"{name}.csv").open("w", newline="", encoding="utf-8") as handle:
                writer = csv.DictWriter(handle, fieldnames=HEADERS[name])
                writer.writeheader()
                writer.writerows(rows)
            os.chmod(folder / f"{name}.csv", 0o600)
        (folder / "manifest.json").write_text(json.dumps(manifest, separators=(",", ":")) + "\n", encoding="utf-8")
        os.chmod(folder / "manifest.json", 0o600)
        build_report(folder, stamp)
        os.chmod(folder, 0o700)
        folder.rename(output)
    return {"snapshot_at": stamp, "mapped_skus": len(mapping), "storefront_found": len(shop_rows),
            "channel_found": len(google_rows), "path": str(output)}
