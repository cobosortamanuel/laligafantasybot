"""Last-minute bidding: bid right at the close, not before.

Idea: don't reveal your bid early. A cron job launches this ~5 min before market
close and decides the amount based on the competition, at two checkpoints:

  - If there are bids from others → bid a competitive amount (value + margin),
    without exceeding your cap (`max_bid`). Don't wait: competition forces a move.
  - If there are NO bids with ~15s left → bid value + a touch (cushion) and done.

The timing is deterministic: it spends no LLM tokens. The agent only picks which
players and with what cap; this runs the finish.
"""

import threading
import time
from datetime import datetime, timezone

from .api import FantasyClient
from . import events, state

CONTESTED_MARGIN_PCT = 0.03   # how far above value to bid if there's competition
UNCONTESTED_CUSHION = 210     # minimum cushion if nobody else bids (+210 EUR rule)
DEFAULT_FINAL = 25           # seconds before close for the finish (safety margin for slow servers)
DEFAULT_POLL = 3             # how often, in seconds, to poll in the final minute


def decide(value, other_bids, seconds_left, max_bid, final=DEFAULT_FINAL, close_window=300):
    """How much to bid NOW, or None to wait.

    - seconds_left > close_window (5 min) → always wait; never leak bid early!
    - other_bids > 0 and <= close_window → competition: value + margin, capped at max_bid.
    - no competition and <= final s left → value + minimum cushion.
    - otherwise, wait.
    """
    if seconds_left > close_window:
        return None
    min_required = value + UNCONTESTED_CUSHION
    if other_bids > 0:
        competitive = value + max(UNCONTESTED_CUSHION, round(value * CONTESTED_MARGIN_PCT))
        bid_target = max(min_required, competitive)
        if max_bid and bid_target > max_bid:
            bid_target = max(min_required, max_bid)
        return bid_target
    if seconds_left <= final:
        bid_target = min_required
        if max_bid and bid_target > max_bid:
            bid_target = max_bid
        return max(value, bid_target)
    return None


def _seconds_left(close_iso):
    # A malformed/missing close time must never crash the bid loop. Unknown -> treat as
    # "plenty of time left" (non-urgent), so decide() won't fire the final-window bid.
    try:
        close = datetime.fromisoformat(close_iso)
    except (TypeError, ValueError):
        return 3600.0
    if close.tzinfo is None:  # naive timestamp -> assume UTC to avoid a TypeError subtract
        close = close.replace(tzinfo=timezone.utc)
    return (close - datetime.now(timezone.utc)).total_seconds()


def _find(market, market_id):
    for e in market:
        if str(e.get("id")) == str(market_id):
            return e
    return None


def last_minute_bid(league_id, market_id, max_bid, value=None, final=DEFAULT_FINAL,
                    poll=DEFAULT_POLL, dry_run=False, log=print, nombre=None):
    """Watches until close and places the bid at the optimal moment."""
    fc = FantasyClient()
    fixed_value = value
    el = _find(fc.market(league_id), market_id)
    if not el:
        log(f"[bid] marketId {market_id} is not in the market (already closed?).")
        return None
    close_iso = el.get("expirationDate")
    pm = el.get("playerMaster") or {}
    nombre = nombre or pm.get("nickname") or pm.get("name") or f"Jugador #{market_id}"
    if not close_iso:
        log(f"[bid] {nombre}: no close date; can't time it. Done.")
        return None

    def _current_value(row):
        if fixed_value is not None:
            return fixed_value
        sale = row.get("salePrice") or 0
        mval = (row.get("playerMaster") or {}).get("marketValue") or 0
        return max(sale, mval) or None

    while True:
        el = _find(fc.market(league_id), market_id)
        if not el:
            log(f"[bid] {nombre}: no longer in the market. Done.")
            return None
        value = _current_value(el)
        if not value:
            log(f"[bid] {nombre}: no market value; can't price a bid.")
            return None
        left = _seconds_left(close_iso)
        other_bids = el.get("numberOfBids", 0)
        amount = decide(value, other_bids, left, max_bid, final)
        if amount is not None:
            if amount < value:
                amount = value + UNCONTESTED_CUSHION
            if dry_run:
                log(f"[bid] {nombre}: WOULD BID {amount:,} "
                    f"(other_bids={other_bids}, {int(left)}s left)")
                return {"dry_run": True, "amount": amount, "other_bids": other_bids}
            try:
                resp = fc.make_bid(league_id, market_id, amount)
                log(f"[bid] {nombre}: BID {amount:,} placed "
                    f"(value {value:,}, other_bids={other_bids}, {int(left)}s left)")
                events.emit("bid", f"Puja de último minuto: {amount:,} € por {nombre}",
                            detail={"rival_bids": other_bids, "time_left": f"{int(left)}s"})
                return resp
            except Exception as e:
                log(f"[bid] {nombre}: Aviso al enviar puja ({amount:,} €): {e}")
                return None
        if left <= 0:
            log(f"[bid] {nombre}: market closed without bidding.")
            return None
        # In automated cloud runs: if the auction closes in more than 60s, don't block the runner
        if left > 60:
            log(f"[bid] {nombre}: cierre programado en {int(left)}s (plan guardado para el cierre).")
            return None
        
        wait = min(poll, max(1, left - final))
        time.sleep(wait)


def run_bid_plan(league_id, dry_run=False, log=print):
    """Runs the saved bid plan: one thread per target (simultaneous closes)."""
    plan = state.load_bid_plan()
    if not plan:
        return
    log(f"[bid] running plan: {len(plan)} targets")
    threads = []
    for t in plan:
        th = threading.Thread(target=last_minute_bid, kwargs=dict(
            league_id=league_id, market_id=t["market_id"], max_bid=t["max_bid"],
            dry_run=dry_run, log=log, nombre=t.get("nombre")))
        th.start()
        threads.append(th)
    for th in threads:
        th.join()

