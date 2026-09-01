"""Date of the next matchday from futbolfantasy.

Used for two things:
  - Signing urgency: the fewer days left, the more room to overpay.
  - Knowing when the FINAL lineup must be set (before the first match).

The date is on each match's page as text
("15 de agosto del 2026 a las 19:30"). Cached 6h.
"""

import re
from datetime import datetime, timezone, timedelta

from .. import config, net, cache

CACHE_TTL = 21600  # 6h
try:
    from zoneinfo import ZoneInfo
    SPAIN_TZ = ZoneInfo("Europe/Madrid")   # respects summer/winter time (CET/CEST)
except Exception:                          # no tz database (Windows without 'tzdata'): fixed CEST
    SPAIN_TZ = timezone(timedelta(hours=2))
MATCH_URL = "https://www.futbolfantasy.com/partidos/{slug}"

MONTHS = {"enero": 1, "febrero": 2, "marzo": 3, "abril": 4, "mayo": 5, "junio": 6,
          "julio": 7, "agosto": 8, "septiembre": 9, "octubre": 10, "noviembre": 11,
          "diciembre": 12}


def _parse_match_datetime(html):
    m = re.search(r"(\d{1,2}) de (\w+) del (\d{4}) a las (\d{1,2}):(\d{2})",
                  html, re.I)
    if not m:
        return None
    day, mon, year, hh, mm = m.groups()
    month = MONTHS.get(mon.lower())
    if not month:
        return None
    return datetime(int(year), month, int(day), int(hh), int(mm), tzinfo=SPAIN_TZ)


def _match_slugs(limit=10):
    """Slugs of upcoming matches (lowest id = nearest matchday). Returns [] if the index
    can't be fetched, so callers degrade to 'unknown' instead of raising (a scraper hiccup
    must never crash the daily review)."""
    try:
        html = net.get(config.FF_LINEUPS_INDEX)
    except Exception:
        return []
    slugs = re.findall(r"/partidos/(\d+-[a-z0-9-]+)", html)
    # sort by numeric id and keep the first ones (one matchday)
    uniq = sorted(set(slugs), key=lambda s: int(s.split("-")[0]))
    return uniq[:limit]


def _compute_next_kickoff():
    now = datetime.now(timezone.utc)
    times = []
    for slug in _match_slugs():
        try:
            dt = _parse_match_datetime(net.get(MATCH_URL.format(slug=slug)))
            if dt:
                times.append(dt)
        except Exception:
            continue
    if not times:
        return None
    future = [t for t in times if t > now]
    return (min(future) if future else min(times)).isoformat()


def next_kickoff(client=None):
    """datetime (ISO) of the first match of the next matchday, or None."""
    if client:
        try:
            cal = client.calendar()
            now = datetime.now(timezone.utc)
            times = []
            for m in cal:
                m_date = m.get("matchDate") or m.get("date") or m.get("time")
                if m_date:
                    try:
                        dt = datetime.fromisoformat(m_date)
                        if dt.tzinfo is None:
                            dt = dt.replace(tzinfo=timezone.utc)
                        times.append(dt)
                    except Exception:
                        pass
            if times:
                future = [t for t in times if t > now]
                if future:
                    return min(future).isoformat()
                return min(times).isoformat()
        except Exception:
            pass
    return cache.cached("next_kickoff", CACHE_TTL, _compute_next_kickoff)


def days_until_matchday(client=None):
    """Days (float) until the first match, or None if it couldn't be obtained."""
    iso = next_kickoff(client)
    if not iso:
        return None
    dt = datetime.fromisoformat(iso)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return (dt - datetime.now(timezone.utc)).total_seconds() / 86400


# --- next GAMEWEEK (jornada) start ------------------------------------------------
# next_kickoff() is the nearest match of ANY jornada. But a jornada can be spread over
# many days (postponed matches), and jornadas can even overlap — jornada 1 might still
# have matches next week while jornada 2 has already begun. What matters for locking a
# lineup and for the "balance must be >= 0 at kickoff" rule is the start of the next
# jornada that HASN'T begun yet, not just the next match on the calendar.

def _pick_next_gameweek_start(first_by_jornada, now):
    """From {jornada_number: first_match_dt}, the kickoff of the next gameweek that has
    NOT started — the earliest per-jornada first match still in the future. A jornada
    whose first match already passed has begun (its lineup is locked), so it's skipped.
    None if every known gameweek has already started."""
    future = [dt for dt in first_by_jornada.values() if dt and dt > now]
    return min(future) if future else None


# The lineups index also lists OTHER competitions (e.g. Premier League), whose match
# pages ALSO carry a bare "Jornada N" (their own matchweek). Mixing a PL matchweek into
# the LaLiga map would pick the wrong gameweek — so require the LaLiga context explicitly
# (the page header reads e.g. "LaLiga 2026/27 - Jornada 2").
_LALIGA_JORNADA = re.compile(r"LaLiga[^<]{0,40}[Jj]ornada\s*(\d+)")


def _first_match_by_jornada(max_laliga=30, max_fetch=60):
    """{jornada_number: earliest LaLiga match datetime}. Scans upcoming match pages,
    keeping ONLY LaLiga ones, until it has `max_laliga` of them — enough to span several
    jornadas so the next unstarted one is found even though slug-id order is NOT kickoff
    order (a jornada's true first match can sit late in its id block)."""
    first, seen = {}, 0
    for slug in _match_slugs(limit=max_fetch):
        try:
            html = net.get(MATCH_URL.format(slug=slug))
        except Exception:
            continue
        m = _LALIGA_JORNADA.search(html)
        if not m:
            continue  # not a LaLiga match (or unparseable) -> skip
        dt = _parse_match_datetime(html)
        if dt is None:
            continue
        jor = int(m.group(1))
        if jor not in first or dt < first[jor]:
            first[jor] = dt
        seen += 1
        if seen >= max_laliga:
            break
    return first


def _compute_next_gameweek_kickoff():
    picked = _pick_next_gameweek_start(_first_match_by_jornada(),
                                       datetime.now(timezone.utc))
    return picked.isoformat() if picked else None


def next_gameweek_kickoff():
    """ISO datetime of the first match of the next gameweek (jornada) that hasn't started
    yet — the deadline that matters for the lineup lock and for keeping the balance >= 0.
    Mid-jornada this points at the FOLLOWING jornada, not the current one's remaining
    matches. Cached 6h. None if it can't be determined."""
    return cache.cached("next_gameweek_kickoff", CACHE_TTL, _compute_next_gameweek_kickoff)
