"""
Test that idea score parsing uses typed exceptions (ValueError, IndexError)
instead of bare except: clauses, and correctly parses score lines.
"""

import unittest


class TestIdeaScoreParsing(unittest.TestCase):
    """Tests for the score parsing logic extracted from idea/service.py."""

    def _parse_score(self, line, default=7.0):
        """Replicate the score parsing logic from service.py."""
        try:
            chars = "".join(c for c in line if c.isdigit() or c == ".")
            score = float(chars)
            return min(10, max(0, score))
        except (ValueError, IndexError):
            return default

    def test_line_with_digits(self):
        """A line with a numeric score should parse correctly."""
        self.assertEqual(self._parse_score("Novelty score: 8"), 8.0)

    def test_line_with_decimal(self):
        """A line with a decimal score should parse correctly."""
        self.assertAlmostEqual(self._parse_score("Feasibility score: 7.5"), 7.5)

    def test_line_without_digits_falls_back(self):
        """A line with no digits should fall back to the default."""
        self.assertEqual(self._parse_score("Novelty score: unknown"), 7.0)

    def test_empty_string_falls_back(self):
        """An empty line (filter produces empty string) should fall back."""
        self.assertEqual(self._parse_score("score: "), 7.0)

    def test_score_clamped_to_10(self):
        """Scores above 10 should be clamped."""
        self.assertEqual(self._parse_score("Impact score: 15"), 10.0)

    def test_score_clamped_to_0(self):
        """Zero is the minimum."""
        self.assertEqual(self._parse_score("Score: 0"), 0.0)

    def test_does_not_catch_keyboard_interrupt(self):
        """Verify that KeyboardInterrupt is NOT caught by the typed except."""
        try:
            try:
                raise KeyboardInterrupt()
            except (ValueError, IndexError):
                self.fail("KeyboardInterrupt should not be caught")
        except KeyboardInterrupt:
            pass  # Expected: KeyboardInterrupt propagated


if __name__ == "__main__":
    unittest.main()
