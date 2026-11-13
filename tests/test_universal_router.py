"""
Enterprise AI Hub 3.0 - Universal Router & Model Router Tests
Verifies that:
1. Enterprise database queries route to ENTERPRISE_DATA
2. Arithmetic queries route to PURE_MATH
3. Ambiguous export queries route to FILE_EXPORT_CLARIFICATION
4. Coding, tutoring, general knowledge, and writing route to UNIVERSAL_CONVERSATIONAL
5. Model router correctly handles provider ordering, vision prioritization, and failover
"""

import sys
import os
import unittest
from pathlib import Path

# Add server directory to path
SERVER_DIR = Path(__file__).resolve().parent.parent / "server"
if str(SERVER_DIR) not in sys.path:
    sys.path.insert(0, str(SERVER_DIR))

from agent.universal_router import classify_query_route, QueryRoute
from agent.model_router import IntelligentModelRouter


class TestUniversalRouter(unittest.TestCase):
    def setUp(self):
        self.mock_endpoints = [
            {"endpoint_name": "Employees Directory", "description": "Staff details, salaries, departments"},
            {"endpoint_name": "PRODUCTS", "description": "Products, pricing, stock, categories"},
            {"endpoint_name": "Google Map Leads", "description": "Business leads, contact information, ratings"}
        ]

    def test_pure_math_routing(self):
        res = classify_query_route("calculate 4500 * 12", self.mock_endpoints)
        self.assertEqual(res["route"], QueryRoute.PURE_MATH)

    def test_file_export_clarification_routing(self):
        res = classify_query_route("Give me the data as a PDF:", self.mock_endpoints)
        self.assertEqual(res["route"], QueryRoute.FILE_EXPORT_CLARIFICATION)

    def test_enterprise_data_routing(self):
        # Explicit table mention
        res1 = classify_query_route("show employee headcount by department", self.mock_endpoints)
        self.assertEqual(res1["route"], QueryRoute.ENTERPRISE_DATA)

        # Entity mention
        res2 = classify_query_route("what is the total payroll of all staff", self.mock_endpoints)
        self.assertEqual(res2["route"], QueryRoute.ENTERPRISE_DATA)

        # Product stock query
        res3 = classify_query_route("which products are in stock", self.mock_endpoints)
        self.assertEqual(res3["route"], QueryRoute.ENTERPRISE_DATA)

        # Earner & breakdown query (top earners must route to enterprise database)
        res4 = classify_query_route("List top 5 earners with breakdown", self.mock_endpoints, general_ai_enabled=True)
        self.assertEqual(res4["route"], QueryRoute.ENTERPRISE_DATA)

        res5 = classify_query_route("List top 5 earners with breakdown", self.mock_endpoints, general_ai_enabled=False)
        self.assertEqual(res5["route"], QueryRoute.ENTERPRISE_DATA)

    def test_general_ai_off_strictly_locks_to_enterprise_data(self):
        # When General AI is OFF, non-db questions must be strictly routed to ENTERPRISE_DATA for security guard rejection
        res_off = classify_query_route("Who is the richest person in the world", self.mock_endpoints, general_ai_enabled=False)
        self.assertEqual(res_off["route"], QueryRoute.ENTERPRISE_DATA)
        self.assertIn("Strict internal database mode", res_off["reason"])

        # Weather is blocked when General AI is OFF
        res_weather = classify_query_route("what is the weather today", self.mock_endpoints, general_ai_enabled=False)
        self.assertEqual(res_weather["route"], QueryRoute.ENTERPRISE_DATA)

        # Foreign non-PSX stock (e.g. Apple / AAPL) is blocked when General AI is OFF
        res_aapl = classify_query_route("what is the stock price of Apple AAPL", self.mock_endpoints, general_ai_enabled=False)
        self.assertEqual(res_aapl["route"], QueryRoute.ENTERPRISE_DATA)

    def test_whitelisted_feeds_when_general_ai_off(self):
        # 1. Currency / Forex feed is allowed when General AI is OFF
        res_fx = classify_query_route("USD to PKR weekly trend", self.mock_endpoints, general_ai_enabled=False)
        self.assertEqual(res_fx["route"], QueryRoute.WEB_RESEARCH)
        self.assertIsNotNone(res_fx["forex_intent"])

        # 2. Fuel feed is allowed when General AI is OFF
        res_fuel = classify_query_route("what is the petrol price today", self.mock_endpoints, general_ai_enabled=False)
        self.assertEqual(res_fuel["route"], QueryRoute.WEB_RESEARCH)
        self.assertIsNotNone(res_fuel["fuel_intent"])

        # 3. PSX Stock feed is allowed when General AI is OFF
        res_psx = classify_query_route("what is the share price of OGDC on PSX", self.mock_endpoints, general_ai_enabled=False)
        self.assertEqual(res_psx["route"], QueryRoute.WEB_RESEARCH)
        self.assertEqual(res_psx["stock_intent"], "OGDC")

        # General AI ON enables weather and external web
        res_weather_on = classify_query_route("what is the weather today", self.mock_endpoints, general_ai_enabled=True)
        self.assertEqual(res_weather_on["route"], QueryRoute.WEB_RESEARCH)
        self.assertIsNotNone(res_weather_on["weather_intent"])

    def test_universal_conversational_routing(self):
        # Coding question
        res_code = classify_query_route("write a python script to parse JSON and sort by key", self.mock_endpoints)
        self.assertEqual(res_code["route"], QueryRoute.UNIVERSAL_CONVERSATIONAL)

        # Tutoring / Education
        res_tutor = classify_query_route("explain how transformer attention mechanisms work in simple terms", self.mock_endpoints)
        self.assertEqual(res_tutor["route"], QueryRoute.UNIVERSAL_CONVERSATIONAL)

        # Creative / Professional Writing
        res_write = classify_query_route("draft a professional apology email to a client for delayed delivery", self.mock_endpoints)
        self.assertEqual(res_write["route"], QueryRoute.UNIVERSAL_CONVERSATIONAL)

        # General Knowledge
        res_gk = classify_query_route("what is the distance between the Earth and the Moon", self.mock_endpoints)
        self.assertEqual(res_gk["route"], QueryRoute.UNIVERSAL_CONVERSATIONAL)

    def test_model_router_ordering(self):
        router = IntelligentModelRouter()
        # Mock providers
        router._provider_cache = [
            {"provider_id": 1, "provider_name": "Groq Primary", "model_name": "llama-3.3-70b-versatile"},
            {"provider_id": 2, "provider_name": "OpenRouter Vision", "model_name": "claude-3.5-sonnet"},
            {"provider_id": 3, "provider_name": "Groq Secondary", "model_name": "gpt-oss-120b"}
        ]
        router._cache_timestamp = 9999999999.0

        # Normal selection
        normal = router.select_best_provider(requires_vision=False)
        self.assertEqual(len(normal), 3)

        # Vision selection should prioritize provider 2 (claude-3.5-sonnet)
        vision = router.select_best_provider(requires_vision=True)
        self.assertEqual(vision[0]["provider_id"], 2)


if __name__ == "__main__":
    unittest.main()
