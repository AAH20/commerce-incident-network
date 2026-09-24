import tempfile
import shutil
import json
import unittest
from pathlib import Path

from commerce_incident_network.core import DataError
from commerce_incident_network.desk import render_desk, sync, transition, view


FIXTURE = Path(__file__).resolve().parents[1] / "fixtures" / "demo"


class DeskTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.db = Path(self.temp.name) / "private" / "desk.sqlite"

    def test_manual_action_requires_separate_approval_and_new_snapshot(self):
        first = sync(self.db, FIXTURE / "day1", "2026-09-24T12:00:00Z", "operator")
        self.assertEqual(first["new"], 3)
        book = next(row for row in view(self.db)["cases"] if row["kind"] == "channel_disapproved")
        transition(self.db, book["id"], "assign", "lead", owner="alice")
        with self.assertRaisesRegex(DataError, "different approver"):
            transition(self.db, book["id"], "approve_manual_fix", "alice", note="Check Merchant Center")
        transition(self.db, book["id"], "approve_manual_fix", "bob", note="Merchant reviewed source listing")
        with self.assertRaisesRegex(DataError, "assigned owner"):
            transition(self.db, book["id"], "record_manual_fix", "bob", note="Updated feed elsewhere")
        result = transition(self.db, book["id"], "record_manual_fix", "alice", note="Corrected merchant feed manually")
        self.assertEqual(result["state"], "awaiting_verification")
        with self.assertRaisesRegex(DataError, "newer complete snapshot"):
            sync(self.db, FIXTURE / "day1", "2026-09-24T12:00:00Z", "operator")
        second = sync(self.db, FIXTURE / "day2", "2026-09-25T12:00:00Z", "operator")
        self.assertEqual(second["resolved_observed"], 2)
        book = next(row for row in view(self.db)["cases"] if row["kind"] == "channel_disapproved")
        self.assertEqual(book["state"], "resolved_observed")
        self.assertEqual(book["owner"], "alice")
        actions = [event["action"] for event in view(self.db)["recent_events"]]
        self.assertIn("record_manual_fix", actions)
        self.assertIn("resolved_observed", actions)

    def test_desk_html_escapes_untrusted_event_note(self):
        sync(self.db, FIXTURE / "day1", "2026-09-24T12:00:00Z", "operator")
        book = view(self.db)["cases"][0]
        transition(self.db, book["id"], "assign", "lead", owner="<script>x</script>")
        page = render_desk(view(self.db))
        self.assertNotIn("<script>x</script>", page)
        self.assertIn("&lt;script&gt;x&lt;/script&gt;", page)

    def test_changed_incident_invalidates_prior_approval(self):
        sync(self.db, FIXTURE / "day1", "2026-09-24T12:00:00Z", "operator")
        mug = next(row for row in view(self.db)["cases"] if row["kind"] == "price_mismatch")
        transition(self.db, mug["id"], "assign", "lead", owner="alice")
        transition(self.db, mug["id"], "approve_manual_fix", "bob", note="Approved correction to USD 18")
        next_folder = Path(self.temp.name) / "changed"
        shutil.copytree(FIXTURE / "day1", next_folder)
        manifest = json.loads((next_folder / "manifest.json").read_text())
        manifest["snapshot_at"] = "2026-09-25T10:00:00Z"
        (next_folder / "manifest.json").write_text(json.dumps(manifest))
        path = next_folder / "channel.csv"
        path.write_text(path.read_text().replace("mug-b,20.00", "mug-b,22.00"))
        sync(self.db, next_folder, "2026-09-25T12:00:00Z", "operator")
        mug = next(row for row in view(self.db)["cases"] if row["kind"] == "price_mismatch")
        self.assertEqual(mug["state"], "assigned")
        self.assertIsNone(mug["approver"])
        self.assertIn("approval_invalidated", [e["action"] for e in view(self.db)["recent_events"]])


if __name__ == "__main__":
    unittest.main()
