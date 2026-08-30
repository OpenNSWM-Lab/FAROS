"""
Test that dynamic_eval.py blocks shell injection via metacharacter denylist
and uses list-args subprocess invocation instead of shell=True.
"""

import os
import sys
import tempfile
import unittest

# Add parent directory to path so we can import the module
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.code.eval.dynamic_eval import DynamicEvaluator, ExecutionStatus


class TestDynamicEvalShellInjection(unittest.TestCase):
    """Tests for shell injection prevention in dynamic_eval.py."""

    def setUp(self):
        self.evaluator = DynamicEvaluator(timeout=10)
        self.temp_dir = tempfile.mkdtemp()

    def tearDown(self):
        import shutil
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_command_with_semicolon_is_blocked(self):
        """Commands containing semicolons should be rejected."""
        result = self.evaluator._run_command(
            "echo hello; rm -rf /", self.temp_dir
        )
        self.assertEqual(result.status, ExecutionStatus.ERROR)
        self.assertIn("dangerous", result.stderr.lower())

    def test_command_with_pipe_is_blocked(self):
        """Commands containing pipe characters should be rejected."""
        result = self.evaluator._run_command(
            "echo hello | cat", self.temp_dir
        )
        self.assertEqual(result.status, ExecutionStatus.ERROR)
        self.assertIn("dangerous", result.stderr.lower())

    def test_command_with_and_operator_is_blocked(self):
        """Commands containing && should be rejected."""
        result = self.evaluator._run_command(
            "echo hello && echo world", self.temp_dir
        )
        self.assertEqual(result.status, ExecutionStatus.ERROR)
        self.assertIn("dangerous", result.stderr.lower())

    def test_command_with_backtick_is_blocked(self):
        """Commands containing backticks should be rejected."""
        result = self.evaluator._run_command(
            "echo `whoami`", self.temp_dir
        )
        self.assertEqual(result.status, ExecutionStatus.ERROR)
        self.assertIn("dangerous", result.stderr.lower())

    def test_command_with_dollar_paren_is_blocked(self):
        """Commands containing $( should be rejected."""
        result = self.evaluator._run_command(
            "echo $(whoami)", self.temp_dir
        )
        self.assertEqual(result.status, ExecutionStatus.ERROR)
        self.assertIn("dangerous", result.stderr.lower())

    def test_normal_command_executes_correctly(self):
        """Normal commands without metacharacters should execute."""
        result = self.evaluator._run_command(
            "echo hello", self.temp_dir
        )
        self.assertEqual(result.status, ExecutionStatus.SUCCESS)
        self.assertEqual(result.stdout.strip(), "hello")

    def test_list_command_executes_correctly(self):
        """Commands passed as lists should execute without shell=True."""
        result = self.evaluator._run_command(
            ["echo", "hello"], self.temp_dir
        )
        self.assertEqual(result.status, ExecutionStatus.SUCCESS)
        self.assertEqual(result.stdout.strip(), "hello")


if __name__ == "__main__":
    unittest.main()
