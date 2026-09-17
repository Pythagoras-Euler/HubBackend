from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from trucky_time import normalize_time, time_seconds


class TruckyTimeTests(unittest.TestCase):
    def test_fractional_and_nonfractional_z_and_offsets_represent_same_instant(self):
        for value in ["2025-09-01T12:00:00Z", "2025-09-01T12:00:00.000000Z",
                      "2025-09-01T14:00:00+02:00", "2025-09-01T12:00:00"]:
            self.assertEqual(normalize_time(value), "2025-09-01T12:00:00Z")
            self.assertEqual(time_seconds(value), 1756728000)

    def test_preserves_fraction_and_crosses_date_boundary(self):
        self.assertEqual(normalize_time("2025-09-01T01:00:00.123456+02:00"), "2025-08-31T23:00:00.123456Z")

    def test_missing_time_is_not_invented(self):
        for value in [None, ""]:
            self.assertIsNone(normalize_time(value))
            self.assertIsNone(time_seconds(value))


if __name__ == "__main__":
    unittest.main()
