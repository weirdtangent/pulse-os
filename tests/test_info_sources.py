from __future__ import annotations

from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from pulse.assistant import info_sources


class ExpandGeocodeQueriesTest(unittest.TestCase):
    def test_city_state_variants_include_plain_city(self) -> None:
        queries = info_sources._expand_geocode_queries("Roanoke, VA")
        self.assertIn("Roanoke, VA", queries)
        self.assertIn("Roanoke", queries)
        self.assertIn("Roanoke VA", queries)
        self.assertIn("Roanoke, VA USA", queries)

    def test_extra_whitespace_produces_single_query(self) -> None:
        queries = info_sources._expand_geocode_queries("  Berlin  ")
        self.assertEqual(["Berlin"], queries)


if __name__ == "__main__":
    unittest.main()

