import json
import tempfile
import unittest
from pathlib import Path

from commerce_incident_network.capture import capture_snapshot, fetch_google, fetch_shopify
from commerce_incident_network.core import DataError, build_report


FIXTURE = Path(__file__).resolve().parents[1] / "fixtures" / "demo" / "day1"


def shop_node(sku, price, qty, published=True):
    return {"node": {"sku": sku, "price": price, "inventoryQuantity": qty,
                     "inventoryPolicy": "DENY", "inventoryItem": {"tracked": True},
                     "product": {"status": "ACTIVE", "publishedAt": "2026-09-01T00:00:00Z" if published else None}}}


def google_product(offer, price_micros, status="approved", availability="IN_STOCK"):
    return {"offerId": offer, "contentLanguage": "en", "feedLabel": "US",
            "productAttributes": {"price": {"currencyCode": "USD", "amountMicros": str(price_micros)}, "availability": availability},
            "productStatus": {"destinationStatuses": [{"reportingContext": "SHOPPING_ADS",
                          "approvedCountries": ["US"] if status == "approved" else [],
                          "disapprovedCountries": ["US"] if status == "disapproved" else [],
                          "pendingCountries": ["US"] if status == "pending" else []}]}}


class CaptureTests(unittest.TestCase):
    def test_shopify_and_google_pagination(self):
        shop_calls = []
        def shop_request(url, token, payload):
            self.assertEqual(token, "shop-secret")
            shop_calls.append(payload["variables"]["cursor"])
            if len(shop_calls) == 1:
                return {"data": {"shop": {"currencyCode": "USD"}, "productVariants": {
                    "edges": [shop_node("BOOK-A", "30.00", 20)], "pageInfo": {"hasNextPage": True, "endCursor": "cursor-1"}}}}
            return {"data": {"shop": {"currencyCode": "USD"}, "productVariants": {
                "edges": [shop_node("MUG-B", "18.00", 8)], "pageInfo": {"hasNextPage": False, "endCursor": "cursor-2"}}}}
        rows = fetch_shopify("test-shop.myshopify.com", "shop-secret", {"BOOK-A", "MUG-B"}, request=shop_request)
        self.assertEqual(shop_calls, [None, "cursor-1"])
        self.assertEqual(rows["BOOK-A"]["published"], "true")
        google_calls = []
        def google_request(url, token):
            self.assertEqual(token, "google-secret")
            google_calls.append(url)
            if len(google_calls) == 1:
                return {"products": [google_product("book-a", 30_000_000, "disapproved")], "nextPageToken": "page 2"}
            return {"products": [google_product("mug-b", 20_000_000)]}
        rows = fetch_google("12345", "google-secret", {"book-a", "mug-b"}, "US", "SHOPPING_ADS", "en", "US", request=google_request)
        self.assertEqual(len(google_calls), 2)
        self.assertIn("pageToken=page+2", google_calls[1])
        self.assertEqual(rows["book-a"]["approval_status"], "disapproved")

    def test_reject_ambiguous_api_data_and_host(self):
        with self.assertRaisesRegex(DataError, "hostname"):
            fetch_shopify("shop.myshopify.com.evil.example", "secret", {"A"}, request=lambda *args: {})
        with self.assertRaisesRegex(DataError, "GraphQL returned errors"):
            fetch_shopify("shop.myshopify.com", "secret", {"A"}, request=lambda *args: {"errors": [{"message": "bad"}]})
        bad = google_product("a", 10_000_000)
        bad["productStatus"]["destinationStatuses"][0]["approvedCountries"] = []
        with self.assertRaisesRegex(DataError, "ambiguous approval"):
            fetch_google("123", "token", {"a"}, "US", "SHOPPING_ADS", "en", "US", request=lambda *args: {"products": [bad]})
        sale = google_product("a", 10_000_000)
        sale["productAttributes"]["salePrice"] = {"currencyCode": "USD", "amountMicros": "9000000"}
        with self.assertRaisesRegex(DataError, "salePrice"):
            fetch_google("123", "token", {"a"}, "US", "SHOPPING_ADS", "en", "US", request=lambda *args: {"products": [sale]})

    def test_capture_publishes_only_complete_valid_snapshot(self):
        with tempfile.TemporaryDirectory() as temp:
            output = Path(temp) / "snapshot"
            def shop_request(url, token, payload):
                return {"data": {"shop": {"currencyCode": "USD"}, "productVariants": {
                    "edges": [shop_node("BOOK-A", "30.00", 20), shop_node("MUG-B", "18.00", 8), shop_node("LAMP-C", "45.00", 0)],
                    "pageInfo": {"hasNextPage": False, "endCursor": None}}}}
            def google_request(url, token):
                return {"products": [google_product("book-a", 30_000_000, "disapproved"),
                                     google_product("mug-b", 20_000_000), google_product("lamp-c", 45_000_000)]}
            result = capture_snapshot(FIXTURE, output, shop_domain="shop.myshopify.com", account_id="123",
                merchant_id="demo", country="US", context="SHOPPING_ADS", language="en", feed_label="US",
                shop_token="shop-token", google_token="google-token", shop_request=shop_request, google_request=google_request)
            self.assertEqual(result["mapped_skus"], 3)
            report = build_report(output, result["snapshot_at"])
            self.assertEqual({item["kind"] for item in report["incidents"]},
                             {"channel_disapproved", "price_mismatch", "availability_mismatch"})
            self.assertTrue(json.loads((output / "manifest.json").read_text())["channel_complete"])
            with self.assertRaisesRegex(DataError, "immutable"):
                capture_snapshot(FIXTURE, output, shop_domain="shop.myshopify.com", account_id="123",
                    merchant_id="demo", country="US", context="SHOPPING_ADS", language="en", feed_label="US",
                    shop_token="shop-token", google_token="google-token", shop_request=shop_request, google_request=google_request)

    def test_failed_capture_leaves_no_snapshot(self):
        with tempfile.TemporaryDirectory() as temp:
            output = Path(temp) / "snapshot"
            with self.assertRaises(DataError):
                capture_snapshot(FIXTURE, output, shop_domain="shop.myshopify.com", account_id="123", merchant_id="demo",
                    country="US", context="SHOPPING_ADS", language="en", feed_label="US", shop_token="shop-token",
                    google_token="google-token", shop_request=lambda *args: {"errors": [{"message": "failed"}]})
            self.assertFalse(output.exists())


if __name__ == "__main__":
    unittest.main()
