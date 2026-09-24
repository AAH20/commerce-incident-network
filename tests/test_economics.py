import tempfile
import unittest
from pathlib import Path

from commerce_incident_network.core import DataError
from commerce_incident_network.economics import derive_mpos_economics


class EconomicsTests(unittest.TestCase):
    def test_derives_current_margin_and_30_day_units_for_mapped_skus(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source = root / "mpos"
            source.mkdir()
            (source / "catalog.csv").write_text(
                "sku,title,description,brand,product_url,image_url,price_usd,unit_cost_usd,fulfillment_cost_usd,inventory_quantity,availability,updated_at\n"
                "A,Example,Description,Brand,https://example.com/a,https://example.com/a.jpg,30.00,10.00,2.00,10,in_stock,2026-09-24\n")
            (source / "orders.csv").write_text(
                "order_id,date,market,sku,quantity,unit_price_usd,discount_usd,shipping_revenue_usd,refund_usd,shipping_cost_usd\n"
                "one,2026-09-24,US,A,3,30.00,0.00,0.00,0.00,2.00\n"
                "two,2026-08-26,US,A,2,30.00,0.00,0.00,0.00,2.00\n"
                "three,2026-09-24,EG,A,5,30.00,0.00,0.00,0.00,2.00\n")
            mapping = root / "mapping.csv"
            mapping.write_text("sku,offer_id\nA,offer-a\n")
            output = root / "economics.csv"
            result = derive_mpos_economics(source, mapping, output, as_of_date="2026-09-24", market="US", attest_complete_orders=True)
            self.assertEqual(result["rows"], 1)
            self.assertIn("A,18.00,5", output.read_text())

    def test_requires_completeness_attestation(self):
        with self.assertRaisesRegex(DataError, "attestation"):
            derive_mpos_economics(Path("unused"), Path("mapping.csv"), Path("out.csv"),
                                  as_of_date="2026-09-24", market="US", attest_complete_orders=False)


if __name__ == "__main__":
    unittest.main()
