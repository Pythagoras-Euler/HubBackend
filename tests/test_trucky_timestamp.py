"""Exercise timestamp conversion without importing the application's services."""
import ast
from datetime import datetime, timezone
from pathlib import Path
import time
import unittest

source = Path(__file__).resolve().parents[1] / "src/functions/tracker.py"
tree = ast.parse(source.read_text(encoding="utf-8"))
function = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "get_job_timestamp")
namespace = {"datetime": datetime, "timezone": timezone, "time": time}
exec(compile(ast.Module(body=[function], type_ignores=[]), str(source), "exec"), namespace)
get_job_timestamp = namespace["get_job_timestamp"]


class TruckyTimestampTests(unittest.TestCase):
    def test_historical_import_keeps_delivery_date(self):
        expected = int(datetime(2026, 8, 12, 12, 19, tzinfo=timezone.utc).timestamp())
        self.assertEqual(get_job_timestamp({"stop_time": "2026-08-12T12:19:00Z"}, "trucky"), expected)

    def test_offset_and_naive_utc_are_consistent(self):
        expected = get_job_timestamp({"stop_time": "2026-08-12T12:19:00Z"}, "trucky")
        for value in ["2026-08-12T14:19:00+02:00", "2026-08-12T12:19:00"]:
            self.assertEqual(get_job_timestamp({"stop_time": value}, "trucky"), expected)

    def test_invalid_source_time_does_not_silently_use_import_date(self):
        with self.assertRaises(ValueError):
            get_job_timestamp({"stop_time": "invalid"}, "trucky")

    def test_other_trackers_use_business_time_and_reject_missing_dates(self):
        expected = int(datetime(2025, 9, 12, tzinfo=timezone.utc).timestamp())
        self.assertEqual(get_job_timestamp({"stop_time": "2025-09-12"}, "tracksim"), expected)
        with self.assertRaises(KeyError):
            get_job_timestamp({}, "tracksim")



if __name__ == "__main__":
    unittest.main()
