import sys
import unittest
from pathlib import Path

SERVER_DIR = Path(__file__).resolve().parent.parent / "server"
if str(SERVER_DIR) not in sys.path:
    sys.path.insert(0, str(SERVER_DIR))

from agent.external_intelligence import (
    extract_forex_intent,
    extract_fuel_intent,
    extract_stock_intent,
    is_psx_stock
)
from agent.universal_router import classify_query_route, QueryRoute


class TestWhitelistedFeeds(unittest.TestCase):
    def setUp(self):
        self.mock_endpoints = [
            {"endpoint_name": "Employees Directory", "description": "Staff details, salaries, departments"},
            {"endpoint_name": "PRODUCTS", "description": "Products, pricing, stock, categories"},
            {"endpoint_name": "Google Map Leads", "description": "Business leads, contact information, ratings"}
        ]

    def test_forex_intent_triggers(self):
        # Suggested chip query
        fx = extract_forex_intent("USD to PKR weekly trend")
        self.assertIsNotNone(fx)
        self.assertEqual(fx["base"], "USD")
        self.assertEqual(fx["target"], "PKR")

        # Conversational query
        fx2 = extract_forex_intent("what is the dollar price right now")
        self.assertIsNotNone(fx2)
        self.assertEqual(fx2["base"], "USD")

    def test_fuel_intent_triggers(self):
        fuel = extract_fuel_intent("what is the petrol price today")
        self.assertIsNotNone(fuel)

    def test_psx_stock_classification(self):
        # Valid PSX stock
        self.assertTrue(is_psx_stock("OGDC"))
        self.assertTrue(is_psx_stock("LUCK"))
        self.assertTrue(is_psx_stock("ILP"))
        self.assertTrue(is_psx_stock("PSX_INDEX"))
        self.assertTrue(is_psx_stock("KSE-100"))

        # Foreign non-PSX stock
        self.assertFalse(is_psx_stock("AAPL"))
        self.assertFalse(is_psx_stock("TSLA"))
        self.assertFalse(is_psx_stock("AMZN"))

    def test_routing_with_general_ai_off(self):
        # Whitelisted feed 1: Forex
        r_fx = classify_query_route("USD to PKR weekly trend", self.mock_endpoints, general_ai_enabled=False)
        self.assertEqual(r_fx["route"], QueryRoute.WEB_RESEARCH)

        # Whitelisted feed 2: Fuel
        r_fuel = classify_query_route("petrol price today in Pakistan", self.mock_endpoints, general_ai_enabled=False)
        self.assertEqual(r_fuel["route"], QueryRoute.WEB_RESEARCH)

        # Whitelisted feed 3: PSX
        r_psx = classify_query_route("share price of OGDC on PSX", self.mock_endpoints, general_ai_enabled=False)
        self.assertEqual(r_psx["route"], QueryRoute.WEB_RESEARCH)

        # Strictly BLOCKED when General AI is OFF: Weather
        r_weather = classify_query_route("what is the weather in lahore", self.mock_endpoints, general_ai_enabled=False)
        self.assertEqual(r_weather["route"], QueryRoute.ENTERPRISE_DATA)

        # Strictly BLOCKED when General AI is OFF: Foreign Stock
        r_foreign = classify_query_route("share price of Tesla TSLA", self.mock_endpoints, general_ai_enabled=False)
        self.assertEqual(r_foreign["route"], QueryRoute.ENTERPRISE_DATA)

        # Strictly BLOCKED when General AI is OFF: General Chit-Chat / Trivia
        r_trivia = classify_query_route("who won the 1992 cricket world cup", self.mock_endpoints, general_ai_enabled=False)
        self.assertEqual(r_trivia["route"], QueryRoute.ENTERPRISE_DATA)


if __name__ == "__main__":
    unittest.main()
