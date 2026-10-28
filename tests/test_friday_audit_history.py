import unittest
from unittest.mock import patch

import pandas as pd

from audits._historical_data import fetch_history


class FridayAuditHistoryTests(unittest.TestCase):
    def _candles(self):
        timestamps = pd.date_range("2026-01-01T00:00:00Z", periods=10, freq="1D")
        return pd.DataFrame({
            "timestamp": timestamps,
            "open": [10.0] * len(timestamps),
            "high": [11.0] * len(timestamps),
            "low": [9.0] * len(timestamps),
            "close": [10.5] * len(timestamps),
            "volume": [100.0] * len(timestamps),
        })

    @patch("audits._historical_data.fetch_ohlc")
    def test_returns_full_window_with_kraken_provenance(self, fetch):
        fetch.return_value = self._candles()
        result = fetch_history("ETHUSDT", "1D", "2026-01-01", "2026-01-10")
        self.assertEqual(len(result), 9)
        self.assertEqual(result.attrs["source"], "Kraken Spot")
        fetch.assert_called_once_with(pair="ETHUSDT", interval=1440)

    @patch("audits._historical_data.fetch_ohlc")
    def test_rejects_partial_history_instead_of_silently_using_it(self, fetch):
        fetch.return_value = self._candles()
        with self.assertRaisesRegex(RuntimeError, "Incomplete Kraken history"):
            fetch_history("ETHUSDT", "1D", "2025-01-01", "2026-01-10")

    def test_rejects_non_usdt_pair(self):
        with self.assertRaisesRegex(ValueError, "Only USDT-quoted pairs"):
            fetch_history("ETHUSD", "1D", "2026-01-01", "2026-01-10")


if __name__ == "__main__":
    unittest.main()
