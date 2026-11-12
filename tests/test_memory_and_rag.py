"""
Tests for Memory Engine and Hybrid RAG Engine
Verifies that:
1. User memory is partitioned by user_id and sensitive secrets are redacted.
2. User memory can be added, retrieved, and deleted.
3. RAG engine slices documents into overlapping semantic chunks with page tracking.
4. RAG search enforces multi-tenant user access boundaries (user A cannot see user B's documents).
"""

import sys
import unittest
from pathlib import Path

SERVER_DIR = Path(__file__).resolve().parent.parent / "server"
if str(SERVER_DIR) not in sys.path:
    sys.path.insert(0, str(SERVER_DIR))

from agent.memory_engine import UserMemoryStore, sanitize_memory_text
from agent.rag_engine import EnterpriseRAGIndex


class TestMemoryAndRAG(unittest.TestCase):
    def test_memory_secret_redaction(self):
        raw = "User password is MySecretPass123 and api_key='sk-abcdef1234567890abcdef1234567890'"
        sanitized = sanitize_memory_text(raw)
        self.assertNotIn("MySecretPass123", sanitized)
        self.assertNotIn("sk-abcdef", sanitized)
        self.assertIn("[REDACTED_SECRET]", sanitized)

    def test_user_memory_lifecycle(self):
        mem_store = UserMemoryStore(user_id=99999)
        mem_store.clear_all()

        # Add memory
        entry = mem_store.add_memory("Prefers dark mode and concise summaries", category="PREFERENCE")
        self.assertIsNotNone(entry)
        self.assertEqual(len(mem_store.get_active_memories()), 1)

        # Retrieve relevant context
        ctx = mem_store.retrieve_relevant_context("give me a concise summary")
        self.assertIn("dark mode and concise summaries", ctx)

        # Delete
        deleted = mem_store.delete_memory(entry["id"])
        self.assertTrue(deleted)
        self.assertEqual(len(mem_store.get_active_memories()), 0)

    def test_rag_chunking_and_tenant_isolation(self):
        rag = EnterpriseRAGIndex()
        sample_doc = (
            "--- Page 1 ---\n"
            "Enterprise financial results show revenue of $50M in Q3 with 20% margin.\n"
            "--- Page 2 ---\n"
            "Operational division headcount expanded by 150 engineers across Islamabad and Lahore.\n"
        )
        # Ingest for user 101
        chunks = rag.chunk_document(sample_doc, "Q3_Report.pdf", user_id=101, session_id="sess_1")
        self.assertTrue(len(chunks) >= 2)

        # User 101 searches -> Should find chunks
        res_u1 = rag.search("financial revenue margin", user_id=101, session_id="sess_1")
        self.assertTrue(len(res_u1) > 0)
        self.assertEqual(res_u1[0]["document_name"], "Q3_Report.pdf")
        self.assertEqual(res_u1[0]["page_num"], 1)

        # User 202 searches same query -> MUST return 0 results (Tenant Isolation!)
        res_u2 = rag.search("financial revenue margin", user_id=202, session_id="sess_1")
        self.assertEqual(len(res_u2), 0, "Security Violation: User 202 was able to retrieve User 101's document!")


if __name__ == "__main__":
    unittest.main()
