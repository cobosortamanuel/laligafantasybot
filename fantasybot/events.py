"""Agent event trace for the supervision UI ("mission control").

Every meaningful action (review, lineup, bid, sniping, sale, buyout) appends ONE
JSON line to `.state/events.jsonl`. The UI (`fantasybot watch`) reads that file
and follows it live over SSE.

It's fantasybot's NATIVE trace: it doesn't depend on Hermes or any external
format, so it works the same with or without an agent on top. That makes it
stable and reproducible: what you see is what the CLI actually did.
"""

import json
import os
import time
from datetime import datetime, timezone, timedelta

try:
    from zoneinfo import ZoneInfo
    SPAIN_TZ = ZoneInfo("Europe/Madrid")
except Exception:
    SPAIN_TZ = timezone(timedelta(hours=2))

from . import config

STATE_DIR = os.path.join(config.ROOT, ".state")
EVENTS_PATH = os.path.join(STATE_DIR, "events.jsonl")
RUN_PATH = os.path.join(STATE_DIR, "run.current")
RUN_IDLE = 20 * 60      # s without activity => treated as a new agent session
MAX_EVENTS = 5000       # trims the file so it doesn't grow forever


def _run_id() -> str:
    """Groups the actions of a single agent cycle under one session id.

    An agent review chains several CLI calls (`agent --json`, `optimize --apply`,
    `bid-plan`...), each in its own process. They share a `run` as long as no more
    than RUN_IDLE min pass without activity; then a new one begins.
    """
    now = time.time()
    cur = None
    if os.path.exists(RUN_PATH):
        try:
            with open(RUN_PATH, encoding="utf-8") as f:
                cur = json.load(f)
        except (ValueError, OSError):
            cur = None
    if cur and (now - cur.get("ts", 0)) < RUN_IDLE:
        rid = cur["id"]
    else:
        rid = datetime.fromtimestamp(now, timezone.utc).strftime("run_%Y%m%d_%H%M%S")
    try:
        os.makedirs(STATE_DIR, exist_ok=True)
        with open(RUN_PATH, "w", encoding="utf-8") as f:
            json.dump({"id": rid, "ts": now}, f)
    except OSError:
        pass
    return rid


def emit(kind: str, title: str, detail=None, status: str = "ok") -> dict:
    """Append an event to the trace. Never raises: telemetry must not break an action.

    kind: read | review | lineup | bid | cancel | bid-plan | sell | clause | note | error
    status: ok | plan | error
    """
    ev = {
        "ts": time.time(),
        "iso": datetime.now(SPAIN_TZ).isoformat(),
        "run": _run_id(),
        "kind": kind,
        "title": title,
        "status": status,
    }
    if detail is not None:
        ev["detail"] = detail
    try:
        os.makedirs(STATE_DIR, exist_ok=True)
        with open(EVENTS_PATH, "a", encoding="utf-8") as f:
            f.write(json.dumps(ev, ensure_ascii=False) + "\n")
        _trim()
    except OSError:
        pass
    return ev


def _trim():
    try:
        with open(EVENTS_PATH, encoding="utf-8") as f:
            lines = f.readlines()
        if len(lines) > MAX_EVENTS:
            with open(EVENTS_PATH, "w", encoding="utf-8") as f:
                f.writelines(lines[-MAX_EVENTS:])
    except OSError:
        pass


def load(limit: int = 500) -> list:
    """Last `limit` events, in chronological order."""
    if not os.path.exists(EVENTS_PATH):
        return []
    out = []
    try:
        with open(EVENTS_PATH, encoding="utf-8") as f:
            lines = f.readlines()
    except OSError:
        return []
    for line in lines[-limit:]:
        line = line.strip()
        if not line:
            continue
        try:
            out.append(json.loads(line))
        except ValueError:
            pass
    return out
