"""
Tests for Sandboxed Code Interpreter
Verifies that:
1. Normal Python code executes and returns stdout.
2. Syntax errors and runtime exceptions are safely caught.
3. Socket network attempts are blocked by security policy.
4. Infinite loops hit the execution timeout.
"""

import sys
import unittest
from pathlib import Path

SERVER_DIR = Path(__file__).resolve().parent.parent / "server"
if str(SERVER_DIR) not in sys.path:
    sys.path.insert(0, str(SERVER_DIR))

from agent.code_interpreter import execute_sandboxed_code


class TestCodeInterpreter(unittest.TestCase):
    def test_basic_execution(self):
        code = "print(sum([x * 2 for x in range(10)]))"
        res = execute_sandboxed_code(code, timeout_sec=5.0)
        self.assertTrue(res["success"])
        self.assertEqual(res["stdout"].strip(), "90")

    def test_syntax_error_handling(self):
        code = "def broken_code(: print('error')"
        res = execute_sandboxed_code(code, timeout_sec=5.0)
        self.assertFalse(res["success"])
        self.assertIn("SyntaxError", res["stderr"])

    def test_network_block_guard(self):
        code = """
import socket
s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
"""
        res = execute_sandboxed_code(code, timeout_sec=5.0)
        self.assertFalse(res["success"])
        self.assertIn("PermissionError", res["stderr"])

    def test_timeout_protection(self):
        code = """
import time
while True:
    time.sleep(0.1)
"""
        res = execute_sandboxed_code(code, timeout_sec=2.0)
        self.assertFalse(res["success"])
        self.assertTrue(res["timed_out"])


if __name__ == "__main__":
    unittest.main()
