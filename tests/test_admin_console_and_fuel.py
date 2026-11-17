import unittest
import os
import sys
import json
from pathlib import Path

# Ensure server module path is available
SERVER_DIR = Path(__file__).resolve().parent.parent / "server"
if str(SERVER_DIR) not in sys.path:
    sys.path.insert(0, str(SERVER_DIR))

from agent.external_intelligence import fetch_live_fuel_prices, _FUEL_PRICE_CACHE
from ui_app import _get_app_config, AdminSettingsRequest

class TestAdminConsoleAndFuelRates(unittest.TestCase):
    def setUp(self):
        _FUEL_PRICE_CACHE.clear()

    def test_official_pso_fuel_rates(self):
        """Validates that fetch_live_fuel_prices returns verified OGRA / PSO rates."""
        fuel = fetch_live_fuel_prices("Pakistan")
        self.assertIsInstance(fuel, dict)
        self.assertIn("petrol", fuel)
        self.assertIn("diesel", fuel)
        self.assertIn("hi_octane", fuel)
        self.assertIn("kerosene", fuel)
        self.assertIn("ldo", fuel)
        self.assertIn("source", fuel)

        # Official rate benchmarks
        self.assertIn("330", fuel["petrol"])
        self.assertIn("360", fuel["diesel"])
        self.assertIn("399", fuel["hi_octane"])
        self.assertIn("283", fuel["kerosene"])
        self.assertIn("231", fuel["ldo"])
        self.assertIn("PSO", fuel["source"])

    def test_app_config_structure(self):
        """Validates that app_config holds complete identity and file export branding fields."""
        cfg = _get_app_config()
        self.assertIn("app_name", cfg)
        self.assertIn("company_name", cfg)
        self.assertIn("footer_watermark", cfg)
        self.assertIn("owner_name", cfg)
        self.assertIn("topbar_mark", cfg)
        self.assertIn("footer_tagline", cfg)
        self.assertIn("contact_email", cfg)

    def test_admin_settings_request_model(self):
        """Validates AdminSettingsRequest Pydantic serialization."""
        req = AdminSettingsRequest(
            owner_name="Test Architect",
            app_name="Custom AI Portal",
            company_name="Apex Global Corp",
            watermark_text="STRICTLY CONFIDENTIAL",
            footer_tagline="Custom legal disclaimer"
        )
        self.assertEqual(req.owner_name, "Test Architect")
        self.assertEqual(req.company_name, "Apex Global Corp")
        self.assertEqual(req.watermark_text, "STRICTLY CONFIDENTIAL")

if __name__ == "__main__":
    unittest.main()
