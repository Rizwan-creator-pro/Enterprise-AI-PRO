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
from agent.weather_helper import extract_weather_cities, extract_weather_intent
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

    def test_forex_disambiguation_avoids_agency_and_market_rates(self):
        """Ensure market rate comparison or product service queries do NOT trigger forex converter."""
        q_agency = "compare our Mobile App Development price (800,000 PKR) against market rates for app agencies in Pakistan online"
        self.assertIsNone(extract_forex_intent(q_agency), "Agency market rate comparison must NOT trigger forex intent!")

        q_prod = "list all products with price in PKR"
        self.assertIsNone(extract_forex_intent(q_prod), "Product database query must NOT trigger forex intent!")

    def test_stock_disambiguation_avoids_ceo_and_net_worth(self):
        """Ensure questions about CEOs, executive leadership, or net worth do NOT trigger stock quote cards."""
        q_ceo = "who is the CEO of Tesla and what is their current net worth?"
        self.assertIsNone(extract_stock_intent(q_ceo), "CEO and net worth queries must NOT hijack stock ticker intent!")

        q_stock = "what is the share price of Tesla today"
        self.assertEqual(extract_stock_intent(q_stock), "TSLA")

        q_psx = "give me the share price of PSO"
        self.assertEqual(extract_stock_intent(q_psx), "PSO")

    def test_multi_city_weather_extraction(self):
        """Ensure queries mentioning multiple cities extract all cities in prompt order."""
        cities = extract_weather_cities("what is the weather today in Karachi and Lahore")
        self.assertEqual(cities, ["Karachi", "Lahore"])

        self.assertEqual(extract_weather_intent("what is the weather today in Karachi and Lahore"), "Karachi")

    def test_stock_history_does_not_hijack_general_questions(self):
        """Ensure queries with words like 'it' or 'its' do NOT pull previous stock from history."""
        hist = [
            {"content": "who is the CEO of Tesla and what is their current net worth?"},
            {"content": "Tesla trades at $390.22 USD"}
        ]
        q_apex = "what is the latest version of Oracle APEX and its key features?"
        self.assertIsNone(extract_stock_intent(q_apex, hist), "Oracle APEX question must NOT trigger Tesla stock!")

        q_pres = "who is the president of the United States?"
        self.assertIsNone(extract_stock_intent(q_pres, hist), "President question must NOT trigger Tesla stock!")

        q_flutter = "what is the difference between Flutter and React Native in 2026?"
        self.assertIsNone(extract_stock_intent(q_flutter, hist), "Flutter comparison must NOT trigger Tesla stock!")

        q_kse = "what is the current status of KSE-100 index"
        self.assertEqual(extract_stock_intent(q_kse, hist), "PSX_INDEX", "KSE-100 index query must resolve to PSX_INDEX!")


if __name__ == "__main__":
    unittest.main()
