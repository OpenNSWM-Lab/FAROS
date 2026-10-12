"""
Test that the experiments render figure endpoint does not inject dummy data
when data_override is None.
"""

import unittest
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))


class TestExperimentsRenderDummyData(unittest.TestCase):
    """Tests for the render figure endpoint data_override handling."""

    def test_data_override_none_not_replaced_with_dummy(self):
        """data_override should not be replaced with dummy fallback."""
        from app.modules.platform import experiments_api
        import inspect
        source = inspect.getsource(experiments_api)
        self.assertNotIn(
            "data_override or [{"'_'": 1}]",
            source,
            "Dummy data fallback should not be present"
        )

    def test_data_override_passed_through_directly(self):
        """generate_figure should receive data_override as-is."""
        from app.modules.platform import experiments_api
        import inspect
        source = inspect.getsource(experiments_api)
        direct_passes = source.count("data_override=data_override,")
        self.assertGreaterEqual(direct_passes, 3)


if __name__ == "__main__":
    unittest.main()
