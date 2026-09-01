"""The agent: a full review, like a human would do when logging in.

`review()` runs an attentive user's cycle:
  1) Looks at WHAT HAS CHANGED since the last connection (signings against you,
     balance...).
  2) Reviews lineup, market, flip opportunities and squad gaps.
  3) Detects buyout targets and works out WHEN to react (reminders).
  4) Keeps the week's task list (adds/completes on its own).

Returns a structured report. Firing the reminders (cronjobs) and the
notifications are built on top (see README / next steps).
"""

from datetime import date, datetime, timedelta

from . import state
from .matching import match_name, POS
from .strategy import captain as captain_mod
from .strategy import flip, needs as needs_mod, sell as sell_mod
from .strategy import lineup as lineup_opt
from .strategy import shield as shield_mod
from .sources.lineups import probable_lineups
from .sources.market_trends import trends_index
from .sources import matchday
from .sources import value_history


def _parse(iso):
    try:
        return datetime.fromisoformat(iso)
    except (TypeError, ValueError):
        return None


def market_close(market):
    """Market close time = nearest expiration of a system player."""
    times = [e["expirationDate"] for e in market
             if e.get("discr") == "marketPlayerLeague" and e.get("expirationDate")]
    return min(times) if times else None


MIN_CLAUSE_PROB = 40  # don't recommend BUYING a player unlikely to start: a benchwarmer
                      # (e.g. a backup keeper at ~10%) scores 0, so a buyout on him is
                      # wasted money. Unknown prob (name unmatched) is kept, not penalised.


def captain_fixture_difficulty(client) -> dict:
    """{team_id: difficulty of the rival THAT team faces this gameweek} for the captain
    picker (see strategy/captain.py). {} on ANY failure (network hiccup, unexpected API
    shape) -- own try/except, separate from `_premium_extras`'s: a captain picked
    without rival-awareness (today's behaviour) is fine, but a crash here must never
    also cost the coach/captain/bench that `_premium_extras` would otherwise still
    build successfully.
    """
    try:
        week = client.current_week() or {}
        fixtures = client.calendar(week.get("weekNumber")) or []
        players = client.all_players() or []
        return captain_mod.fixture_difficulty_by_team(players, fixtures)
    except Exception:
        return {}


def clause_targets(market, team, prob_index):
    """Other managers' players worth signing via buyout clause when it opens.

    v1: the ones that fill a squad gap and you can afford. Each brings its unlock
    time to schedule the reminder.
    """
    gap_positions = set(needs_mod.gaps(team))
    owned = {p["playerMaster"]["id"] for p in team["players"]}
    money = team["teamMoney"]
    targets = []
    for el in market:
        if el["discr"] != "marketPlayerTeam":
            continue
        pm = el["playerMaster"]
        if pm["id"] in owned:
            continue
        pos = POS.get(pm.get("positionId"))
        if pos not in gap_positions:
            continue
        pt = el.get("playerTeam", {})
        clause, unlock = pt.get("buyoutClause"), pt.get("buyoutClauseLockedEndTime")
        # If his owner already has him ON SALE, bidding is the cheaper way in: the
        # clause is a ~1.67x premium and it is locked for days, while the sale is open
        # now and starts at his value. The sale is a DOOR OF ITS OWN: gating on an
        # affordable clause first priced reachable listings out of the report just
        # because their (irrelevant) clause was rich.
        on_sale = el.get("salePrice") if el.get("status") == "on_sale" else None
        via_clausula = bool(clause and unlock and clause <= money)
        via_puja = bool(on_sale and on_sale <= money)
        if not (via_clausula or via_puja):
            continue
        info = match_name(pm.get("nickname", ""), pm.get("name", ""), prob_index)
        prob = info.get("prob") if info else None
        if prob is not None and prob < MIN_CLAUSE_PROB:
            continue  # benchwarmer: signing him by any route is wasted money
        targets.append({
            "nombre": pm.get("nickname") or pm.get("name"),
            "player_id": pm["id"],
            "pos": pos,
            "clause": clause,
            "unlock": unlock,
            "prob": prob,
            "reason": f"fills a {pos} gap",
            # cheaper route, when there is one
            "market_id": el.get("id") if on_sale else None,
            "sale_price": on_sale,
            "sale_expires": el.get("expirationDate") if on_sale else None,
            "cheaper_via_bid": bool(on_sale and via_puja
                                    and (not via_clausula or on_sale < clause)),
            "saving_vs_clause": ((clause - on_sale)
                                 if (clause and on_sale and on_sale < clause) else 0),
        })
    targets.sort(key=lambda t: (t["prob"] or 0), reverse=True)
    return targets


def _sync_tasks(gaps, targets, sells, lineup_changed):
    """Keeps the task list: creates missing ones, closes resolved ones."""
    # squad gaps
    for pos in ("POR", "DEF", "MED", "DEL"):
        key = f"gap:{pos}"
        if pos in gaps:
            state.add_task(f"Sign {pos}: you're short in that position.", key=key)
        else:
            state.complete_by_key(key)
    # buyout targets (and close the ones that no longer apply)
    for t in targets:
        if t.get("cheaper_via_bid"):
            text = (f"Bid for {t['nombre']} ({t['pos']}): he's ON SALE at "
                    f"{t['sale_price']:,}, {t['saving_vs_clause']:,} less than his "
                    f"{t['clause']:,} clause. Closes {t['sale_expires']}.")
            due = t["sale_expires"]
        else:
            text = (f"Buyout {t['nombre']} ({t['pos']}) for {t['clause']:,} "
                    f"when its clause opens.")
            due = t["unlock"]
        state.add_task(text, due=due, key=f"clause:{t['player_id']}")
    state.complete_missing("clause:", {f"clause:{t['player_id']}" for t in targets})
    # recommended sales
    for s in sells:
        state.add_task(f"Sell {s['nombre']} (~{s['sale_price']:,}): {s['reason']}.",
                       key=f"sell:{s['player_id']}")
    state.complete_missing("sell:", {f"sell:{s['player_id']}" for s in sells})
    # lineup
    if lineup_changed:
        state.add_task("Update lineup (there's a better XI).", key="lineup")
    else:
        state.complete_by_key("lineup")


def _current_lineup(client, team_id):
    """Current lineup as (xi_ids, coach_id, captain_id).

    `coach`/`captain` are premium-only fields on the GET formation (absent -> None). Exposed
    so apply_lineup can detect a captain/coach change that leaves the XI unchanged (else we'd
    never PUT the new captain). One API call; both act paths reuse it.
    """
    lu = client.lineup(team_id)
    f = lu.get("formation", {})
    ids = set()
    for pos in ("goalkeeper", "defender", "midfield", "striker"):
        for p in f.get(pos, []) or []:
            ids.add(p.get("playerTeamId") or p["playerMaster"]["id"])
    coach = None
    for c in f.get("coach", []) or []:
        coach = c.get("playerTeamId") or (c.get("playerMaster") or {}).get("id")
    captain = f.get("captain") or None
    return ids, coach, captain


def _current_xi_ids(client, team_id):
    """Just the current XI ids (back-compat wrapper over `_current_lineup`)."""
    return _current_lineup(client, team_id)[0]


def lineup_lock_reminder(kickoff, now=None):
    """The "set your FINAL LINEUP" reminder — but ONLY on the day the matchday's first
    match is actually played.

    During the odd early-season gameweeks the next kickoff can be several days out, and
    surfacing "set your lineup" that early just confuses (a user saw it for a jornada
    that didn't start until 3 days later). We compare calendar DAYS in Spain time, so the
    notice appears on match day itself and not before. Returns the reminder dict, or None.
    """
    dt = _parse(kickoff) if kickoff else None
    if not dt:
        return None
    tz = matchday.SPAIN_TZ
    now = now or datetime.now(tz)
    if dt.astimezone(tz).date() != now.astimezone(tz).date():
        return None  # first match isn't today -> don't nag about the lineup yet
    return {
        "key": f"lineup_lock:{kickoff}",
        "fire_at": (dt - timedelta(hours=2)).isoformat(),
        "event_at": kickoff,
        "message": "Matchday is today: set your FINAL LINEUP.",
    }


def league_allows_premium_formations(client, lid) -> bool:
    """True when this league is premium AND unlocks the extra formations (so the optimizer
    may use the 2-midfielder shapes). Reads config.premiumFeatures.formations from leagues().
    Any error / unknown league / lid None -> False (safe default: standard formations only)."""
    if not lid:
        return False
    try:
        for lg in client.leagues():
            if str(lg.get("id")) == str(lid):
                return bool((lg.get("config") or {}).get("premiumFeatures", {}).get("formations"))
    except Exception:
        return False
    return False


def review(client, days_to_matchday=None):
    lid, tid = client.default_ids()
    team = client.team(lid, tid)
    market = client.market(lid)
    prob_index = probable_lineups()

    # date of the next matchday (for urgency and final lineup)
    try:
        kickoff = matchday.next_kickoff(client)
    except TypeError:
        kickoff = matchday.next_kickoff()
    if days_to_matchday is None:
        try:
            days_to_matchday = matchday.days_until_matchday(client)
        except TypeError:
            days_to_matchday = matchday.days_until_matchday()

    # 1) what has changed
    prev = state.load_snapshot()
    curr = state.snapshot(team)
    events = state.diff_snapshots(prev, curr)
    state.save_snapshot(curr)

    # Bank today's OFFICIAL market values (all_players(), competition-wide — not just our
    # squad) so we build our OWN value history over time, independent of the futbolfantasy
    # scrape. Purely additive collection: a hiccup here must never break the review.
    try:
        players = client.all_players()
        state.save_value_snapshot(date.today().isoformat(),
                                  value_history.snapshot_from_players(players))
    except Exception:
        pass

    # 2) lineup — a squad that can't field a valid XI (e.g. no goalkeeper mid-rebuild)
    # must not crash the whole review: report it and carry on so gaps/needs still fire.
    try:
        premium = league_allows_premium_formations(client, lid)
        fixture_difficulty = captain_fixture_difficulty(client) if premium else None
        best = lineup_opt.optimize(team, prob_index, premium=premium,
                                   fixture_difficulty=fixture_difficulty)
        best_ids = lineup_opt.payload_ids(best)
        lineup_changed = best_ids != _current_xi_ids(client, tid)
        lineup_section = {"formation": best["formation"], "changed": lineup_changed,
                          "total": best["total"], "watch": best.get("watch", [])}
    except ValueError as e:
        best, lineup_changed = None, False
        lineup_section = {"formation": None, "changed": False, "total": 0,
                          "watch": [], "note": str(e)}

    # 3) flips, needs and sales
    owned = {p["playerMaster"]["id"] for p in team["players"]}
    flips = [o for o in flip.opportunities(client, lid, owned=owned)
             if o["margin_pct"] > 0 and o["buy_price"] <= team["teamMoney"]][:5]
    gaps = needs_mod.gaps(team)
    needs_report = needs_mod.advise(client, lid, team, days_to_matchday)
    # A missing lineup (incomplete squad) only skips the lineup itself — sells, flips,
    # clauses and reminders still apply. sell_candidates handles best=None.
    sells = sell_mod.sell_candidates(team, best, trends_index(), prob_index=prob_index)

    # 4) buyout targets + reminders
    targets = clause_targets(market, team, prob_index)
    reminders = []
    close = market_close(market)
    if close:
        dt = _parse(close)
        if dt:
            reminders.append({
                "key": f"market_close:{close}",
                "fire_at": (dt - timedelta(minutes=5)).isoformat(),
                "event_at": close,
                "message": "Market closes in 5 min: review bids and needs.",
            })
    for t in targets:
        if t.get("cheaper_via_bid"):
            continue   # the recommended route is the OPEN SALE; a "prepare the
                       # buyout" alarm for the same player contradicts the task
        dt = _parse(t["unlock"])
        if dt:
            reminders.append({
                "key": f"clause:{t['player_id']}:{t['unlock']}",
                "fire_at": (dt - timedelta(seconds=60)).isoformat(),
                "event_at": t["unlock"],
                "message": (f"{t['nombre']}'s clause opens: prepare a buyout "
                            f"of {t['clause']:,} ({t['reason']})."),
            })
    # The lineup lock is about the NEXT gameweek that hasn't started — not today's match
    # if the current jornada is already under way (its lineup is already locked). A
    # scraper hiccup here must never crash the whole daily review.
    try:
        gw_kickoff = matchday.next_gameweek_kickoff()
    except Exception:
        gw_kickoff = None
    lineup_rem = lineup_lock_reminder(gw_kickoff)
    if lineup_rem:
        reminders.append(lineup_rem)
    reminders.sort(key=lambda r: r["fire_at"])

    _sync_tasks(gaps, targets, sells, lineup_changed)
    state.save_reminders(reminders)

    try:
        from .strategy import rivals as rivals_mod
        rivals_list = rivals_mod.analyze_rivals(client, lid)
    except Exception:
        rivals_list = []

    result = {
        "events": events,
        "money": team["teamMoney"],
        "matchday": {"kickoff": kickoff, "days": days_to_matchday},
        "lineup": lineup_section,
        "flips": flips,
        "gaps": gaps,
        "needs": needs_report,
        "sells": sells,
        "clause_targets": targets,
        "rivals": rivals_list,
        "reminders": reminders,
        "tasks": state.pending_tasks(),
    }

    # 5) defensive shield (blindaje): our most clause-vulnerable valuable player. Only when
    # the squad can actually field an XI (best is not None) — if we can't even line up, the
    # focus is elsewhere (fill the gap first). Reach reuses the `rivals` estimate above (the
    # richest rival's cash) instead of a second API call. Fully guarded: any failure just
    # OMITS the key, it must never break the daily review.
    try:
        if best is not None:
            reach = max(
                ((r.get("estimated_balance") or 0) for r in rivals_list if not r.get("is_me")),
                default=0,
            )
            result["shield"] = shield_mod.shield_candidate(team, reach)
    except Exception:
        pass
    return result
