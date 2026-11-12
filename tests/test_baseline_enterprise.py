"""
Enterprise AI Hub 3.0 - Baseline Enterprise Regression Test Suite
Verifies that all core enterprise routing, SQL engine components, deterministic math,
external tool extractors, and clarification guardrails are functioning accurately.
"""

import sys
import os
import unittest
from pathlib import Path

# Add server directory to path
SERVER_DIR = Path(__file__).resolve().parent.parent / "server"
if str(SERVER_DIR) not in sys.path:
    sys.path.insert(0, str(SERVER_DIR))

# Import core modules
from agent import query_engine as qe
from agent.external_intelligence import (
    extract_forex_intent,
    extract_stock_intent,
    extract_fuel_intent,
    STOCK_SYMBOL_MAP
)
from agent.weather_helper import extract_weather_intent
from ui_app import (
    _match_endpoint,
    _match_endpoints_multi,
    is_pure_math_expression,
    evaluate_math_expression,
    generate_followup_suggestions
)


class TestBaselineEnterpriseFunctionality(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.mock_endpoints = [
            {
                "endpoint_name": "Employees Directory",
                "description": "Staff details, designations, salaries, departments",
                "rest_url": "https://example.com/ords/employees"
            },
            {
                "endpoint_name": "PRODUCTS",
                "description": "Products, pricing, stock, categories",
                "rest_url": "https://example.com/ords/products"
            },
            {
                "endpoint_name": "Google Map Leads",
                "description": "Business leads, contact information, ratings",
                "rest_url": "https://example.com/ords/leads"
            }
        ]

    # 1. Database Schema & Synonym Matching
    def test_schema_synonym_matching(self):
        # Employees synonyms
        m1 = _match_endpoint("show employee headcount", self.mock_endpoints)
        self.assertIsNotNone(m1)
        self.assertEqual(m1["endpoint_name"], "Employees Directory")

        m2 = _match_endpoint("what is the total payroll of staff", self.mock_endpoints)
        self.assertIsNotNone(m2)
        self.assertEqual(m2["endpoint_name"], "Employees Directory")

        # Products synonyms
        m3 = _match_endpoint("list all products in catalog", self.mock_endpoints)
        self.assertIsNotNone(m3)
        self.assertEqual(m3["endpoint_name"], "PRODUCTS")

        # Leads synonyms
        m4 = _match_endpoint("show google map leads with high rating", self.mock_endpoints)
        self.assertIsNotNone(m4)
        self.assertEqual(m4["endpoint_name"], "Google Map Leads")

    def test_multi_table_detection(self):
        multi = _match_endpoints_multi("compare employee salary with product inventory", self.mock_endpoints)
        names = [e["endpoint_name"] for e in multi]
        self.assertIn("Employees Directory", names)
        self.assertIn("PRODUCTS", names)

    # 2. Deterministic Arithmetic Engine (0 Tokens)
    def test_pure_math_engine(self):
        self.assertTrue(is_pure_math_expression("25000 * 1.15"))
        self.assertTrue(is_pure_math_expression("calculate (1500 + 350) / 2"))
        self.assertTrue(is_pure_math_expression("what is 5^3"))

        res = evaluate_math_expression("25000 * 1.15")
        self.assertIsNotNone(res)
        self.assertAlmostEqual(res["eval"], 28750.0)

        res_paren = evaluate_math_expression("(10 + 5) * 4")
        self.assertIsNotNone(res_paren)
        self.assertEqual(res_paren["eval"], 60)

        # Non-math should return False or None
        self.assertFalse(is_pure_math_expression("how many employees are in IT"))

    # 3. Disambiguation & External Intent Negative Overrides
    def test_no_forex_hijacking_on_database_queries(self):
        # Even with currency terms, database entity queries MUST NOT be hijacked by standalone Forex
        q = "give me the employee salary department wise in dollar and also in the pkr"
        fx = extract_forex_intent(q)
        self.assertIsNone(fx, "Database employee compensation query was erroneously classified as forex")

        # Pure forex queries MUST be detected
        q_forex = "what is the dollar rate today"
        fx_pure = extract_forex_intent(q_forex)
        self.assertIsNotNone(fx_pure)
        self.assertEqual(fx_pure["base"], "USD")
        self.assertEqual(fx_pure["target"], "PKR")

    def test_no_stock_hijacking_on_warehouse_inventory(self):
        # Product inventory should NOT trigger stock ticker lookup
        q_inv = "which products are in stock"
        st_inv = extract_stock_intent(q_inv)
        self.assertIsNone(st_inv, "Warehouse inventory query was erroneously classified as equity ticker")

        # Real stock equity queries MUST trigger
        q_stock = "what is the stock price of systems"
        st_real = extract_stock_intent(q_stock)
        self.assertEqual(st_real, "SYS")

    # 4. In-Memory Query Engine & Self-Healing
    def test_query_engine_self_healing(self):
        sample_dataset = [
            {"dept": "IT", "salary": 100000, "status": "IN_STOCK"},
            {"dept": "IT", "salary": 120000, "status": "IN_STOCK"},
            {"dept": "Sales", "salary": 80000, "status": "OUT_OF_STOCK"}
        ]
        # Query with typo / delimiter variation: "in-stock" vs "IN_STOCK"
        plan = {
            "mode": "aggregate",
            "group_by": ["dept"],
            "filters": [{"col": "status", "op": "==", "val": "in-stock"}],
            "aggs": [{"col": "salary", "agg": "sum", "as": "Total Salary"}]
        }
        res = qe.execute_plan(sample_dataset, plan)
        self.assertIsNotNone(res)
        rows = res.get("table_rows") or []
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["dept"], "IT")
        self.assertEqual(rows[0]["Total Salary"], 220000)

    # 5. Follow-Up Suggestions Engine
    def test_followup_suggestions(self):
        suggestions = generate_followup_suggestions("who has the highest salary", self.mock_endpoints)
        self.assertIsInstance(suggestions, list)
        self.assertTrue(len(suggestions) >= 3)
        self.assertTrue(any("salary" in s.lower() for s in suggestions))


if __name__ == "__main__":
    unittest.main()
