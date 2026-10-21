import unittest
from unittest.mock import patch
import pandas as pd

from research.friday_monitor import _utc


class FridayMonitorTests(unittest.TestCase):
    def test_utc_normalizes_naive_timestamp(self):
        self.assertEqual(_utc("2026-10-21T12:00:00").isoformat(), "2026-10-21T12:00:00+00:00")

    def test_utc_normalizes_offset_timestamp(self):
        self.assertEqual(_utc("2026-10-21T13:00:00+01:00").isoformat(), "2026-10-21T12:00:00+00:00")


if __name__ == "__main__":
    unittest.main()
