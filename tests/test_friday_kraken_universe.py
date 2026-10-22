import unittest

from research.friday_kraken_universe import discover_liquid_spot_markets


class FakeKrakenClient:
    def __init__(self):
        self.calls = []

    def get(self, endpoint, params=None):
        self.calls.append((endpoint, params))
        if endpoint == "AssetPairs":
            return {
                "XXBTZUSD": {"status": "online", "quote": "ZUSD", "wsname": "XBT/USD"},
                "XETHZUSD": {"status": "online", "quote": "ZUSD", "wsname": "ETH/USD"},
                "XBADZUSD": {"status": "cancel_only", "quote": "ZUSD", "wsname": "BAD/USD"},
                "XFOOZEUR": {"status": "online", "quote": "ZEUR", "wsname": "FOO/EUR"},
            }
        if endpoint == "Ticker":
            return {
                "XXBTZUSD": {"c": ["65000"], "v": ["10", "100"], "p": ["64000", "64500"]},
                "XETHZUSD": {"c": ["3000"], "v": ["100", "1000"], "p": ["2900", "2950"]},
            }
        raise AssertionError(endpoint)


class FridayKrakenUniverseTests(unittest.TestCase):
    def test_discovers_online_quote_markets_and_ranks_by_turnover(self):
        client = FakeKrakenClient()
        result = discover_liquid_spot_markets(
            client, min_quote_volume_usd=1_000_000, max_markets=10
        )
        self.assertEqual([item.pair for item in result], ["XXBTZUSD", "XETHZUSD"])
        self.assertGreater(result[0].quote_volume_24h, result[1].quote_volume_24h)
        self.assertEqual([call[0] for call in client.calls], ["AssetPairs", "Ticker"])

    def test_minimum_turnover_filters_thin_markets(self):
        result = discover_liquid_spot_markets(
            FakeKrakenClient(), min_quote_volume_usd=10_000_000
        )
        self.assertEqual(result, [])


if __name__ == "__main__":
    unittest.main()
