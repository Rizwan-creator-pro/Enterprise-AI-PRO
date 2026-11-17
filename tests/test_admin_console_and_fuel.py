import unittest
import os
import sys
import json
import base64
from pathlib import Path
from unittest.mock import MagicMock

# Ensure server module path is available
SERVER_DIR = Path(__file__).resolve().parent.parent / "server"
if str(SERVER_DIR) not in sys.path:
    sys.path.insert(0, str(SERVER_DIR))

from fastapi import HTTPException
from agent.external_intelligence import fetch_live_fuel_prices, _FUEL_PRICE_CACHE
from ui_app import _get_app_config, AdminSettingsRequest, LoginRequest, _make_token, login

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

    def test_email_only_login_validation(self):
        """Validates that login strictly requires a valid corporate email address."""
        # Non-email username should be rejected with 400
        req_invalid = LoginRequest(username="admin", password="password123")
        req_mock = MagicMock()
        req_mock.client.host = "127.0.0.1"
        res_mock = MagicMock()
        with self.assertRaises(HTTPException) as ctx:
            login(req_invalid, req_mock, res_mock)
        self.assertEqual(ctx.exception.status_code, 400)
        self.assertIn("email", ctx.exception.detail.lower())

    def test_token_and_email_session(self):
        """Validates that token contains the user's verified corporate email."""
        tok = _make_token(101, "admin", is_admin="Y", email="admin@enterprise.com")
        self.assertIsInstance(tok, str)
        self.assertIn(".", tok)
        body = tok.split(".")[0]
        data = json.loads(base64.urlsafe_b64decode(body.encode()))
        self.assertEqual(data.get("m"), "admin@enterprise.com")
        self.assertEqual(data.get("n"), "admin")
        self.assertEqual(data.get("a"), "Y")

    def test_admin_console_markup_sanitization(self):
        """Validates that extra sidebar and template leftovers are absent from index.html."""
        index_path = Path(__file__).resolve().parent.parent / "client" / "index.html"
        html = index_path.read_text(encoding="utf-8")

        # Ensure redundant inner sidebar is gone
        self.assertNotIn('id="adminSidebar"', html)
        self.assertNotIn('class="admin-sidebar"', html)

        # Ensure template leftovers are gone
        self.assertNotIn('id="adminTopbarMark"', html)
        self.assertNotIn('id="adminResumeLink"', html)
        self.assertNotIn('id="secFooter"', html)
        self.assertNotIn('id="secContact"', html)
        self.assertNotIn('id="adminFooterTagline"', html)
        self.assertNotIn('id="adminContactEmail"', html)

        # Ensure agent-relevant elements are present
        self.assertIn('id="adminOwnerName"', html)
        self.assertIn('id="adminAppName"', html)
        self.assertIn('id="adminCompanyName"', html)
        self.assertIn('id="adminWatermarkText"', html)
        self.assertIn('id="secMasterLink"', html)
        self.assertIn('id="secProviders"', html)
        self.assertIn('id="secExternalApis"', html)
        self.assertIn('id="emailInput"', html)

if __name__ == "__main__":
    unittest.main()
