import sys
import os
import re
import unittest
from pathlib import Path

SERVER_DIR = Path(__file__).resolve().parent.parent / "server"
if str(SERVER_DIR) not in sys.path:
    sys.path.insert(0, str(SERVER_DIR))

from agent import query_engine as qe


def detect_query_scope(question: str):
    q_low_inq = question.lower().strip()
    word_to_num = {"one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7, "eight": 8, "nine": 9, "ten": 10}
    m_num = re.search(r'\b(?:top|first|highest|lowest|best|priciest|cheapest|limit)\s+(\d+)\b', q_low_inq)
    m_word = re.search(r'\b(?:top|first|highest|lowest|best)\s+(one|two|three|four|five|six|seven|eight|nine|ten)\b', q_low_inq)
    explicit_requested_limit = int(m_num.group(1)) if m_num else (word_to_num.get(m_word.group(1)) if m_word else None)

    is_plural_top = (explicit_requested_limit is not None and explicit_requested_limit > 1) or any(k in q_low_inq for k in [
        "top 5", "top 10", "top 3", "top earners", "top products", "top leads", "ranking", "compare top",
        "list top", "all highest", "all lowest", "breakdown", "table form", "all data", "full table",
        "all rows", "each", "all products", "all employees", "all leads", "overview"
    ])

    singular_superlative_patterns = [
        r'\b(?:the|a|an|which|who|what)?\s*(?:most\s+)?(?:expensive|cheapest|costliest|priciest)\s+(?:product|item|offering|package|solution|catalog)\b',
        r'\b(?:the|a|an|which|who|what)?\s*(?:highest|lowest|maximum|minimum|top|best)\s+(?:salary|paid|earner|income|wage|pay|compensation|price|rating|score|review)\b',
        r'\b(?:who|which)\s+(?:has|earns|gets|holds|is)\s+(?:the\s+)?(?:highest|lowest|max|maximum|min|minimum|most|cheapest|best|top|expensive)\b',
        r'\b(?:who|which)\s+is\s+(?:the\s+)?(?:top|highest|lowest|cheapest|most\s+expensive|expensive)\b',
        r'\b(?:top|highest|lowest|cheapest|most\s+expensive|expensive)\s+(?:one|single|individual|employee|staff|worker|product|item|lead)\b',
        r'\b(?:give|show|tell|get|find|display)\s+(?:me\s+)?(?:the\s+)?(?:most\s+)?(?:expensive|cheapest|costliest|priciest|highest|lowest|top|best)\s+(?:product|item|employee|staff|earner|lead)\b'
    ]
    has_singular_pattern = any(bool(re.search(p, q_low_inq)) for p in singular_superlative_patterns)

    singular_keywords = [
        "highest salary", "lowest salary", "minimum salary", "maximum salary",
        "cheapest product", "most expensive product", "expensive product", "the expensive product",
        "priciest product", "costliest product", "highest price", "lowest price",
        "highest paid", "lowest paid", "top earner", "single person", "single product", "single lead",
        "best rated", "highest rating", "top lead", "best lead"
    ]

    is_singular_extremum = (
        explicit_requested_limit == 1
        or has_singular_pattern
        or any(k in q_low_inq for k in singular_keywords)
        or (
            any(k in q_low_inq for k in ["highest", "lowest", "maximum", "minimum", "cheapest", "most expensive", "expensive product", "top earner", "top product"])
            and not is_plural_top
        )
    )
    is_singular_query = bool(is_singular_extremum and not is_plural_top)

    is_table_form_request = (
        not is_singular_query
        and explicit_requested_limit is None
        and any(k in q_low_inq for k in [
            "table form", "in table", "all data", "as a table", "table format",
            "show table", "give me data", "show data", "list data", "in the table form",
            "g map table", "google map table", "table data", "entire table", "full table"
        ])
    )

    return {
        "is_singular_query": is_singular_query,
        "explicit_requested_limit": explicit_requested_limit,
        "is_table_form_request": is_table_form_request
    }


class TestDynamicQuerySizing(unittest.TestCase):
    def test_singular_expensive_product(self):
        # The user's exact query
        scope = detect_query_scope("give me the expensive product")
        self.assertTrue(scope["is_singular_query"])
        self.assertFalse(scope["is_table_form_request"])

    def test_singular_variations(self):
        cases = [
            "the expensive product",
            "expensive product",
            "who is the highest earner",
            "what is the highest salary",
            "cheapest product",
            "priciest product",
            "top earner",
            "single product with highest price",
            "who earns the most"
        ]
        for q in cases:
            with self.subTest(q=q):
                scope = detect_query_scope(q)
                self.assertTrue(scope["is_singular_query"])
                self.assertFalse(scope["is_table_form_request"])

    def test_explicit_requested_limits(self):
        s3 = detect_query_scope("top 3 products")
        self.assertEqual(s3["explicit_requested_limit"], 3)
        self.assertFalse(s3["is_singular_query"])

        s5 = detect_query_scope("first 5 employees")
        self.assertEqual(s5["explicit_requested_limit"], 5)
        self.assertFalse(s5["is_singular_query"])

        s10 = detect_query_scope("top ten earners")
        self.assertEqual(s10["explicit_requested_limit"], 10)
        self.assertFalse(s10["is_singular_query"])

    def test_table_form_requests(self):
        s_table = detect_query_scope("show all data in table form")
        self.assertTrue(s_table["is_table_form_request"])
        self.assertFalse(s_table["is_singular_query"])

    def test_query_engine_singular_execution(self):
        # 15 mock products matching the catalog in screenshot
        mock_products = [
            {"PRODUCT_ID": 1, "PRODUCT_NAME": "Enterprise CRM License", "CATEGORY": "Software", "PRICE": 250000, "STOCK_QTY": 100},
            {"PRODUCT_ID": 2, "PRODUCT_NAME": "Cloud Hosting Basic", "CATEGORY": "Cloud Services", "PRICE": 15000, "STOCK_QTY": 500},
            {"PRODUCT_ID": 3, "PRODUCT_NAME": "Cloud Hosting Premium", "CATEGORY": "Cloud Services", "PRICE": 45000, "STOCK_QTY": 200},
            {"PRODUCT_ID": 4, "PRODUCT_NAME": "Data Analytics Suite", "CATEGORY": "Software", "PRICE": 180000, "STOCK_QTY": 75},
            {"PRODUCT_ID": 5, "PRODUCT_NAME": "Cybersecurity Package", "CATEGORY": "Security", "PRICE": 120000, "STOCK_QTY": 150},
            {"PRODUCT_ID": 6, "PRODUCT_NAME": "IT Support Monthly", "CATEGORY": "Services", "PRICE": 60000, "STOCK_QTY": 999},
            {"PRODUCT_ID": 7, "PRODUCT_NAME": "IT Support Annual", "CATEGORY": "Services", "PRICE": 680000, "STOCK_QTY": 999},
            {"PRODUCT_ID": 8, "PRODUCT_NAME": "Mobile App Development", "CATEGORY": "Services", "PRICE": 800000, "STOCK_QTY": 50},
            {"PRODUCT_ID": 9, "PRODUCT_NAME": "Website Development", "CATEGORY": "Services", "PRICE": 550000, "STOCK_QTY": 80},
            {"PRODUCT_ID": 10, "PRODUCT_NAME": "AI Chatbot License", "CATEGORY": "Software", "PRICE": 300000, "STOCK_QTY": 60},
            {"PRODUCT_ID": 11, "PRODUCT_NAME": "Network Setup", "CATEGORY": "Hardware", "PRICE": 280000, "STOCK_QTY": 40},
            {"PRODUCT_ID": 12, "PRODUCT_NAME": "Server Hardware", "CATEGORY": "Hardware", "PRICE": 490000, "STOCK_QTY": 25},
            {"PRODUCT_ID": 13, "PRODUCT_NAME": "Training Basic", "CATEGORY": "Training", "PRICE": 25000, "STOCK_QTY": 999},
            {"PRODUCT_ID": 14, "PRODUCT_NAME": "Training Advanced", "CATEGORY": "Training", "PRICE": 75000, "STOCK_QTY": 900},
            {"PRODUCT_ID": 15, "PRODUCT_NAME": "Backup Solution", "CATEGORY": "Security", "PRICE": 90000, "STOCK_QTY": 100}
        ]

        # Plan for singular query
        plan = {
            "mode": "raw",
            "target_table": "PRODUCTS",
            "order_by": [{"col": "PRICE", "desc": True}],
            "limit": 1,
            "chart": None
        }

        res = qe.execute_plan(mock_products, plan)
        self.assertEqual(len(res["table_rows"]), 1)
        self.assertEqual(res["table_rows"][0]["PRODUCT_NAME"], "Mobile App Development")
        self.assertEqual(res["table_rows"][0]["PRICE"], 800000)
        self.assertIsNone(res["chart"])


if __name__ == "__main__":
    unittest.main()
