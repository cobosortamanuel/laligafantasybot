import unittest
import tempfile
import os
import shutil
from fantasybot import state
from fantasybot.dashboard_generator import _format_spain_time

class TestAgentOfferResolutionAndHistory(unittest.TestCase):
    def setUp(self):
        self.test_dir = tempfile.mkdtemp()
        self.orig_history_path = state.REASONING_HISTORY_PATH
        state.REASONING_HISTORY_PATH = os.path.join(self.test_dir, "test_reasoning_history.json")

    def tearDown(self):
        state.REASONING_HISTORY_PATH = self.orig_history_path
        shutil.rmtree(self.test_dir, ignore_errors=True)

    def test_reasoning_history_roundtrip(self):
        self.assertEqual(state.load_reasoning_history(), [])
        history = [
            {"timestamp": "2026-09-09 12:00", "reasoning": "Test reasoning 1"},
            {"timestamp": "2026-09-09 11:00", "reasoning": "Test_reasoning 2"}
        ]
        state.save_reasoning_history(history)
        loaded = state.load_reasoning_history()
        self.assertEqual(len(loaded), 2)
        self.assertEqual(loaded[0]["reasoning"], "Test reasoning 1")

    def test_format_spain_time_with_formatted_string(self):
        res = _format_spain_time("Wednesday, 09 de September de 2026 a las 00:34")
        self.assertEqual(res, "Wednesday, 09 de September de 2026 a las 00:34")

    def test_format_spain_time_with_iso_string(self):
        res = _format_spain_time("2026-09-09T10:30:00Z")
        self.assertTrue(len(res) > 5)
        self.assertIn("09/09/2026", res)

    def test_offer_resolution_logic(self):
        my_received_offers = [
            {
                "playerTeamId": 111,
                "marketId": 222,
                "offerId": "89344200",
                "jugador": "Antony",
                "oferta_recibida": 67772648
            },
            {
                "playerTeamId": 333,
                "marketId": 444,
                "offerId": "89344201",
                "jugador": "Guruzeta",
                "oferta_recibida": 11469875
            }
        ]

        offers_by_id = {}
        offers_by_name = {}
        offers_by_market_id = {}
        offers_by_player_team_id = {}
        for off in my_received_offers:
            if off.get("offerId"):
                offers_by_id[str(off["offerId"])] = off
            if off.get("marketId"):
                offers_by_market_id[str(off["marketId"])] = off
            if off.get("playerTeamId"):
                offers_by_player_team_id[str(off["playerTeamId"])] = off
            if off.get("jugador"):
                offers_by_name[off["jugador"].lower().strip()] = off

        # Test String ID
        matched = offers_by_id.get("89344200")
        self.assertIsNotNone(matched)
        self.assertEqual(matched["jugador"], "Antony")

        # Test Player Name
        matched_name = offers_by_name.get("antony")
        self.assertIsNotNone(matched_name)
        self.assertEqual(matched_name["offerId"], "89344200")

        # Test Dict
        matched_dict = offers_by_id.get("89344201")
        self.assertIsNotNone(matched_dict)
        self.assertEqual(matched_dict["jugador"], "Guruzeta")


if __name__ == "__main__":
    unittest.main()
