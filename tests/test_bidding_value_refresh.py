"""last_minute_bid must size the bid off the player's CURRENT value, re-read on every
poll — not the value captured at the first market read. A system auction keeps its
listing salePrice frozen, but the value is re-valued (daily / during the day); if it
climbs while we wait for the close, a bid sized off the stale first read is BELOW value
and LaLiga rejects it ("... is not a valid money quantity for this player", 030.01.01).
"""

import unittest
from datetime import datetime, timedelta, timezone
from unittest import mock

from fantasybot import bidding


class ValueRefreshedEachPoll(unittest.TestCase):
    def _run(self, first, later):
        """Run last_minute_bid where market() returns `first` then `later` on later calls.
        Each is (marketValue, numberOfBids). Returns the amount passed to make_bid."""
        calls = {"n": 0}
        close = (datetime.now(timezone.utc) + timedelta(minutes=5)).isoformat()

        def _row(mv, bids):
            return [{"id": "m1", "expirationDate": close, "numberOfBids": bids,
                     "salePrice": 9_000_000,
                     "playerMaster": {"nickname": "P", "marketValue": mv}}]

        placed = {}

        class FC:
            def market(self, lid):
                calls["n"] += 1
                mv, bids = first if calls["n"] == 1 else later
                return _row(mv, bids)

            def make_bid(self, lid, mid, amount):
                placed["amount"] = amount
                return {"id": "b1"}

        with mock.patch.object(bidding, "FantasyClient", lambda: FC()), \
             mock.patch.object(bidding.events, "emit", lambda *a, **k: {}):
            bidding.last_minute_bid("L", "m1", max_bid=30_000_000)
        return placed.get("amount")

    def test_contested_bid_uses_current_value_not_stale(self):
        # Value rises 10M -> 15M between the setup read and the loop read; a contested
        # auction fires immediately, so it MUST bid off the fresh 15M (>= value), not 10M.
        amount = self._run(first=(10_000_000, 0), later=(15_000_000, 1))
        self.assertIsNotNone(amount)
        self.assertGreaterEqual(amount, 15_000_000)

    def test_stable_value_still_bids_normally(self):
        # No re-valuation: contested bid ~ value + 3% margin off the current 10M.
        amount = self._run(first=(10_000_000, 0), later=(10_000_000, 1))
        self.assertIsNotNone(amount)
        self.assertGreaterEqual(amount, 10_000_000)
        self.assertLessEqual(amount, 11_000_000)


if __name__ == "__main__":
    unittest.main()
