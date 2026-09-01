"""Regression: an incomplete squad (no goalkeeper) must not crash the agent.

A real paying user mid-rebuild had zero goalkeepers; `optimize()` raised
"No goalkeeper in the squad." and the whole daily `agent` run died. The review must
now degrade: no lineup, but the rest (gaps/needs) still reported so the user is told to
sign one.
"""

import tempfile
import unittest
from unittest import mock

from fantasybot import agent as agent_mod
from fantasybot.strategy import lineup as lineup_opt


def _player(pid, position_id, value=1_000_000):
    return {"playerTeamId": f"pt{pid}",
            "playerMaster": {"id": f"p{pid}", "nickname": f"N{pid}", "name": f"N{pid}",
                             "positionId": position_id, "marketValue": value,
                             "playerStatus": "ok"}}


def _squad(with_gk):
    # 2=DEF 3=MID 4=STR ; 1=GK
    players = ([_player("g", 1)] if with_gk else []) \
        + [_player(f"d{i}", 2) for i in range(5)] \
        + [_player(f"m{i}", 3) for i in range(5)] \
        + [_player(f"s{i}", 4) for i in range(3)]
    return {"teamMoney": 3_000_000, "players": players}


class _FakeClient:
    def __init__(self, team):
        self._team = team

    def default_ids(self):
        return ("L", "T")

    def team(self, lid, tid):
        return self._team

    def market(self, lid):
        return []

    def lineup(self, tid):
        return {"formation": {}}


class OptimizeContract(unittest.TestCase):
    def test_raises_without_goalkeeper(self):
        with self.assertRaises(ValueError):
            lineup_opt.optimize(_squad(with_gk=False), prob_index={})

    def test_ok_with_full_squad(self):
        best = lineup_opt.optimize(_squad(with_gk=True), prob_index={})
        self.assertIn("payload", best)
        self.assertEqual(best["payload"]["goalkeeper"], "ptg")


class ReviewResilience(unittest.TestCase):
    def setUp(self):
        # Isolate state regardless of test import order or the repository path.
        state_dir = self.enterContext(tempfile.TemporaryDirectory(prefix="fb-nogk-"))
        for name, filename in [
            ("STATE_DIR", None),
            ("SNAPSHOT_PATH", "snapshot.json"),
            ("TASKS_PATH", "tasks.json"),
            ("REMINDERS_PATH", "reminders.json"),
            ("BIDS_PATH", "bids.json"),
            ("BID_PLAN_PATH", "bid_plan.json"),
        ]:
            value = state_dir if filename is None else f"{state_dir}/{filename}"
            patcher = mock.patch.object(agent_mod.state, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)

        # Stub the external/scraper calls so review() runs fully offline.
        for target, val in [
            ("probable_lineups", lambda *a, **k: {}),
            ("trends_index", lambda *a, **k: {}),
        ]:
            p = mock.patch.object(agent_mod, target, val)
            p.start()
            self.addCleanup(p.stop)
        for target, val in [
            ("probable_lineups", lambda *a, **k: {}),
            ("trends_index", lambda *a, **k: {}),
        ]:
            p = mock.patch.object(agent_mod, target, val)
            p.start()
            self.addCleanup(p.stop)
        for mod, name, val in [
            (agent_mod.matchday, "next_kickoff", lambda: None),
            (agent_mod.matchday, "days_until_matchday", lambda: None),
            (agent_mod.flip, "opportunities", lambda *a, **k: []),
            (agent_mod.needs_mod, "advise",
             lambda *a, **k: {"gaps": {}, "urgency_multiplier": 1, "suggestions": {}}),
        ]:
            p = mock.patch.object(mod, name, val)
            p.start()
            self.addCleanup(p.stop)

    def test_no_gk_review_degrades_instead_of_crashing(self):
        rep = agent_mod.review(_FakeClient(_squad(with_gk=False)))
        # lineup can't be built -> reported, not crashed
        self.assertIsNone(rep["lineup"]["formation"])
        self.assertIn("note", rep["lineup"])
        self.assertFalse(rep["lineup"]["changed"])
        # a missing lineup only skips the lineup — the rest still runs, not crashes
        self.assertIsInstance(rep["sells"], list)
        self.assertIn("POR", rep["gaps"])            # the GK gap IS surfaced

    def test_full_squad_review_still_produces_a_lineup(self):
        rep = agent_mod.review(_FakeClient(_squad(with_gk=True)))
        self.assertIsNotNone(rep["lineup"]["formation"])


class SellWithoutLineup(unittest.TestCase):
    """A missing lineup (best=None) must not silence sell advice: falling-value players
    are still flagged (there are just no protected starters)."""

    def test_falling_player_flagged_when_best_is_none(self):
        from fantasybot.strategy import sell
        team = {"teamMoney": 0, "players": [
            {"playerTeamId": "pt1", "playerMaster": {
                "id": "p1", "nickname": "Falling", "name": "Falling",
                "positionId": 4, "marketValue": 5_000_000}}]}
        trends = {"falling": {"tendencia": -40}}
        with mock.patch.object(sell, "match_name", lambda n, nm, i: trends["falling"]):
            out = sell.sell_candidates(team, None, trends)
        self.assertEqual(len(out), 1)
        self.assertEqual(out[0]["nombre"], "Falling")

if __name__ == "__main__":
    unittest.main()
