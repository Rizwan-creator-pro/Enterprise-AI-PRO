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
        """Validates that fetch_live_fuel_prices dynamically retrieves verified rates from GlobalPetrolPrices and PSO."""
        fuel = fetch_live_fuel_prices("Pakistan")
        self.assertIsInstance(fuel, dict)
        self.assertIn("petrol", fuel)
        self.assertIn("diesel", fuel)
        self.assertIn("source", fuel)
        self.assertIn("effective_date", fuel)
        self.assertIn("timestamp", fuel)

        # Dynamic live rate format validation
        if fuel.get("petrol"):
            self.assertTrue(fuel["petrol"].startswith("Rs.") or "PKR" in fuel["petrol"] or "USD" in fuel["petrol"])
        if fuel.get("diesel"):
            self.assertTrue(fuel["diesel"].startswith("Rs.") or "PKR" in fuel["diesel"] or "USD" in fuel["diesel"])
        self.assertTrue("PSO" in fuel["source"] or "GlobalPetrolPrices" in fuel["source"])

    def test_globalpetrolprices_worldwide_integration(self):
        """Validates that GlobalPetrolPrices.com provides verified international and worldwide rates."""
        # 1. Worldwide average benchmark
        world = fetch_live_fuel_prices("Worldwide")
        self.assertIsInstance(world, dict)
        self.assertEqual(world["country"], "Worldwide (Global Average)")
        self.assertIn("USD", world.get("petrol", ""))
        self.assertIn("GlobalPetrolPrices.com", world.get("source", ""))

        # 2. International country benchmark (USA)
        usa = fetch_live_fuel_prices("USA")
        self.assertIsInstance(usa, dict)
        self.assertEqual(usa["country"], "USA")
        self.assertIn("USD", usa.get("petrol", ""))
        self.assertIn("GlobalPetrolPrices.com", usa.get("source", ""))

    def test_app_config_structure(self):
        """Validates that app_config holds complete identity and file export branding fields."""
        cfg = _get_app_config()
        self.assertIn("app_name", cfg)
        self.assertIn("app_icon", cfg)
        self.assertIn("company_name", cfg)
        self.assertIn("footer_watermark", cfg)
        self.assertIn("owner_name", cfg)
        self.assertIn("topbar_mark", cfg)
        self.assertIn("footer_tagline", cfg)
        self.assertIn("contact_email", cfg)

    def test_admin_settings_request_model(self):
        """Validates AdminSettingsRequest Pydantic serialization with complete enterprise fields."""
        req = AdminSettingsRequest(
            owner_name="Test Architect",
            app_name="Custom AI Portal",
            app_icon="fa-solid fa-robot",
            company_name="Apex Global Corp",
            topbar_mark="apex.corp",
            contact_email="admin@apexcorp.com",
            contact_phone="+92 300 1234567",
            watermark_text="STRICTLY CONFIDENTIAL",
            footer_watermark="Apex AI • Confidential",
            max_upload_images=10,
            max_upload_docs=5,
            footer_tagline="Custom legal disclaimer"
        )
        self.assertEqual(req.owner_name, "Test Architect")
        self.assertEqual(req.app_icon, "fa-solid fa-robot")
        self.assertEqual(req.company_name, "Apex Global Corp")
        self.assertEqual(req.topbar_mark, "apex.corp")
        self.assertEqual(req.contact_email, "admin@apexcorp.com")
        self.assertEqual(req.contact_phone, "+92 300 1234567")
        self.assertEqual(req.watermark_text, "STRICTLY CONFIDENTIAL")
        self.assertEqual(req.footer_watermark, "Apex AI • Confidential")
        self.assertEqual(req.max_upload_images, 10)
        self.assertEqual(req.max_upload_docs, 5)

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
        self.assertNotIn('id="secFooter"', html)
        self.assertNotIn('id="secContact"', html)

        # Ensure agent-relevant identity, branding & upload limit elements are present
        self.assertIn('id="adminOwnerName"', html)
        self.assertIn('id="adminAppName"', html)
        self.assertIn('id="adminAppIcon"', html)
        self.assertIn('id="adminIconPreview"', html)
        self.assertIn('id="adminCompanyName"', html)
        self.assertIn('id="adminWatermarkText"', html)
        self.assertIn('id="adminTopbarMark"', html)
        self.assertIn('id="adminContactEmail"', html)
        self.assertIn('id="adminContactPhone"', html)
        self.assertIn('id="adminMaxUploadImages"', html)
        self.assertIn('id="adminMaxUploadDocs"', html)
        self.assertIn('id="secMasterLink"', html)
        self.assertIn('id="secProviders"', html)
        self.assertIn('id="secExternalApis"', html)
        self.assertIn('id="emailInput"', html)
        self.assertIn('id="loginBrandText"', html)
        self.assertIn('id="loginBrandIcon"', html)

        # Ensure BASE_TITLE is mutable (let, not const) so branding updates don't throw TypeError
        self.assertIn('let BASE_TITLE =', html)
        self.assertNotIn('const BASE_TITLE =', html)

        # Ensure pre-paint brand caching and instant persistence exist
        self.assertIn('applyAppBranding', html)
        self.assertIn('syncAppBrandingConfig', html)
        self.assertIn("localStorage.getItem('ai_app_name')", html)
        self.assertIn("localStorage.getItem('ai_app_icon')", html)

    def test_dynamic_agent_name_prompt_generation(self):
        """Validates that system prompts dynamically inject custom app name and company."""
        from ui_app import _get_synthesizer_prompt, _get_general_synthesizer_prompt
        from agent.universal_router import get_universal_system_prompt

        custom_prompt = get_universal_system_prompt("ApexAgent", "Apex Corp")
        self.assertIn("You are ApexAgent", custom_prompt)
        self.assertIn("Apex Corp", custom_prompt)

        synth = _get_synthesizer_prompt()
        self.assertIsInstance(synth, str)
        self.assertIn("You are", synth)

        gen_synth = _get_general_synthesizer_prompt()
        self.assertIsInstance(gen_synth, str)
        self.assertIn("You are", gen_synth)

if __name__ == "__main__":
    unittest.main()
