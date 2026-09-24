import csv
import json
import shutil
import tempfile
import unittest
from pathlib import Path

from commerce_incident_network.cli import _csv_safe, render_html
from commerce_incident_network.core import DataError, build_report


FIXTURE = Path(__file__).resolve().parents[1] / "fixtures" / "demo"


class IncidentTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.folder = Path(self.temp.name) / "snapshot"
        shutil.copytree(FIXTURE / "day1", self.folder)

    def report(self, previous=None):
        return build_report(self.folder, "2026-09-24T12:00:00Z", previous)

    def edit(self, table, key, value):
        path = self.folder / f"{table}.csv"
        with path.open(newline="", encoding="utf-8") as handle:
            reader = csv.DictReader(handle)
            rows, fields = list(reader), reader.fieldnames
        rows[0][key] = value
        with path.open("w", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(handle, fieldnames=fields)
            writer.writeheader()
            writer.writerows(rows)

    def test_incident_detection_and_priority(self):
        report = self.report()
        self.assertEqual(report["summary"], {"active": 3, "new": 3, "ongoing": 0, "resolved_observed": 0})
        self.assertEqual({r["kind"] for r in report["incidents"]}, {"channel_disapproved", "price_mismatch", "availability_mismatch"})
        self.assertEqual(report["incidents"][0]["kind"], "channel_disapproved")
        self.assertEqual(report["incidents"][0]["daily_margin_priority_proxy_usd"], "24.00")

    def test_recovery_requires_new_complete_snapshot(self):
        old = self.report()
        new = build_report(FIXTURE / "day2", "2026-09-25T12:00:00Z", old)
        self.assertEqual(new["summary"], {"active": 1, "new": 0, "ongoing": 1, "resolved_observed": 2})
        self.assertEqual({r["kind"] for r in new["incidents"] if r["state"] == "resolved_observed"}, {"channel_disapproved", "price_mismatch"})

    def test_stale_snapshot_blocks_false_resolution(self):
        old = self.report()
        with self.assertRaisesRegex(DataError, "stale"):
            build_report(FIXTURE / "day2", "2026-09-28T12:00:00Z", old)

    def test_incomplete_snapshot_blocks_false_resolution(self):
        manifest = json.loads((self.folder / "manifest.json").read_text())
        manifest["channel_complete"] = False
        (self.folder / "manifest.json").write_text(json.dumps(manifest))
        with self.assertRaisesRegex(DataError, "complete"):
            self.report()

    def test_duplicate_offer_mapping_is_rejected(self):
        path = self.folder / "mapping.csv"
        path.write_text("sku,offer_id\nBOOK-A,book-a\nMUG-B,book-a\nLAMP-C,lamp-c\n")
        with self.assertRaisesRegex(DataError, "duplicate offer_id"):
            self.report()

    def test_unmapped_channel_product_is_rejected(self):
        with (self.folder / "channel.csv").open("a") as handle:
            handle.write("unknown,9.00,in_stock,approved\n")
        with self.assertRaisesRegex(DataError, "explicit mapping"):
            self.report()

    def test_missing_economics_removes_priority_proxy(self):
        path = self.folder / "economics.csv"
        path.write_text("sku,unit_margin_usd,units_sold_30d\n")
        report = self.report()
        self.assertTrue(all(r["daily_margin_priority_proxy_usd"] is None for r in report["incidents"]))

    def test_money_precision_and_currency_are_strict(self):
        self.edit("channel", "price_usd", "30.001")
        with self.assertRaisesRegex(DataError, "decimal places"):
            self.report()
        manifest = json.loads((self.folder / "manifest.json").read_text())
        manifest["currency"] = "EUR"
        (self.folder / "manifest.json").write_text(json.dumps(manifest))
        with self.assertRaisesRegex(DataError, "USD only"):
            self.report()

    def test_html_and_csv_escape_untrusted_sku(self):
        attack = "<script>alert(1)</script>"
        for name, column in (("mapping", "sku"), ("storefront", "sku"), ("economics", "sku")):
            self.edit(name, column, attack)
        rendered = render_html(self.report())
        self.assertNotIn(attack, rendered)
        self.assertIn("&lt;script&gt;", rendered)
        self.assertEqual(_csv_safe("=1+2"), "'=1+2")

    def test_previous_scope_mismatch_is_rejected(self):
        old = self.report()
        old["scope"]["merchant_id"] = "other"
        with self.assertRaisesRegex(DataError, "scope differs"):
            build_report(FIXTURE / "day2", "2026-09-25T12:00:00Z", old)


if __name__ == "__main__":
    unittest.main()
