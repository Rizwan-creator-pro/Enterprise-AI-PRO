import unittest
import sys
import os

# Add server directory to path
server_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "server")
if server_dir not in sys.path:
    sys.path.insert(0, server_dir)

from ui_app import (
    _is_dataset_inventory_query,
    _build_security_policy_message,
    _handle_dataset_inventory_query
)
from agent.universal_router import classify_query_route, QueryRoute


class TestDatasetInventoryAndDynamicSecurity(unittest.TestCase):

    def test_dataset_inventory_query_detection(self):
        """Verify that natural inquiries about available data/tables are accurately detected."""
        positive_queries = [
            "what data you have",
            "what data do you have",
            "what data is available",
            "what tables do you have",
            "what tables are available",
            "what datasets do you have",
            "what can you query",
            "what kind of data do you have",
            "what type of data is available",
            "show available tables",
            "list tables",
            "tell me available tables",
            "what data",
            "show data"
        ]
        for q in positive_queries:
            self.assertTrue(
                _is_dataset_inventory_query(q),
                f"Expected query to be detected as dataset inventory: '{q}'"
            )

        negative_queries = [
            "what is the highest salary in our database",
            "who has the highest salary",
            "cheapest product in inventory",
            "what is the weather in Karachi",
            "dollar rate today",
            "who is the CEO of Apple",
            "convert 100 USD to PKR"
        ]
        for q in negative_queries:
            self.assertFalse(
                _is_dataset_inventory_query(q),
                f"Expected query NOT to be detected as dataset inventory: '{q}'"
            )

    def test_dynamic_security_policy_message_filtering(self):
        """Verify that the security message only lists tables assigned to the logged-in user."""
        # Scenario 1: User Rizwan has only 2 tables (Employees Directory and PRODUCTS)
        rizwan_endpoints = [
            {"endpoint_name": "Employees Directory", "endpoint_id": 1},
            {"endpoint_name": "PRODUCTS", "endpoint_id": 2}
        ]
        msg_rizwan = _build_security_policy_message(rizwan_endpoints)

        self.assertIn("Employees Directory", msg_rizwan)
        self.assertIn("PRODUCTS", msg_rizwan)
        self.assertNotIn("Google Map Leads", msg_rizwan, "Google Map Leads must NOT appear when user has only 2 tables!")
        self.assertIn("Verified Business Feeds", msg_rizwan)
        self.assertIn("General AI is Switched Off", msg_rizwan)

        # Scenario 2: Admin user has all 3 tables
        admin_endpoints = [
            {"endpoint_name": "Employees Directory", "endpoint_id": 1},
            {"endpoint_name": "PRODUCTS", "endpoint_id": 2},
            {"endpoint_name": "Google Map Leads", "endpoint_id": 3}
        ]
        msg_admin = _build_security_policy_message(admin_endpoints)
        self.assertIn("Employees Directory", msg_admin)
        self.assertIn("PRODUCTS", msg_admin)
        self.assertIn("Google Map Leads", msg_admin)

        # Scenario 3: Custom endpoint table
        custom_endpoints = [
            {"endpoint_name": "Warehouse_Inventory", "description": "Stock bins and pallet locations"}
        ]
        msg_custom = _build_security_policy_message(custom_endpoints)
        self.assertIn("Warehouse_Inventory", msg_custom)
        self.assertNotIn("Employees Directory", msg_custom)

    def test_handle_dataset_inventory_output(self):
        """Verify that the dataset inventory handler returns clear, formatted table overviews."""
        user_endpoints = [
            {"endpoint_name": "Employees Directory"},
            {"endpoint_name": "PRODUCTS"}
        ]
        inventory_msg = _handle_dataset_inventory_query(user_endpoints, req_outside_enabled=False)

        self.assertIn("### 🗄️ Authorized Enterprise Datasets", inventory_msg)
        self.assertIn("Employees Directory", inventory_msg)
        self.assertIn("PRODUCTS", inventory_msg)
        self.assertNotIn("Google Map Leads", inventory_msg)
        self.assertNotIn("Available Fields:", inventory_msg)
        self.assertNotIn("emp_id", inventory_msg)
        self.assertIn("Verified Business Feeds", inventory_msg)
        self.assertIn("Ask any question about the datasets above", inventory_msg)

    def test_universal_router_routes_inventory_query_to_enterprise_data(self):
        """Verify that 'what data you have' routes directly to ENTERPRISE_DATA, not general chat or rejection."""
        endpoints = [
            {"endpoint_name": "Employees Directory"},
            {"endpoint_name": "PRODUCTS"}
        ]
        route_result = classify_query_route(
            question="what data you have",
            endpoints=endpoints,
            general_ai_enabled=False
        )
        self.assertEqual(route_result["route"], QueryRoute.ENTERPRISE_DATA)


if __name__ == "__main__":
    unittest.main()
