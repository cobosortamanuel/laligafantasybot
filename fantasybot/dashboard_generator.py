"""Apple-Style Minimalist Mobile-Friendly Dashboard Generator for FantasyBot.

Features:
- Mobile-friendly responsive UI with sleek horizontal pill navigation.
- 5 Focused Tabs:
  1. Plantilla & Ofertas (Squad with market listing status and received offers table).
  2. Mercado Libre (System free agents with search, position filter, and real starting probability).
  3. Rivales & Cláusulas (League ranking table + Rival Buyout Clause radar with shield countdown).
  4. Puntos (Gameweek points bar chart + total score breakdown).
  5. Historial (Permanent accumulated Gemini reasoning archive + Action audit timeline).
- Clean SVG vector icons (0 emojis).
- Refined Apple Monochrome / Dark palette (Zinc/Neutral/Emerald minimal accents).
- Fixes starting probability so it never shows false 100%.
"""

import html as html_lib
import json
import os
import re
from datetime import datetime, timezone, timedelta

try:
    from zoneinfo import ZoneInfo
    SPAIN_TZ = ZoneInfo("Europe/Madrid")
except Exception:
    SPAIN_TZ = timezone(timedelta(hours=2))

from . import config, events, state


def _format_spain_time(iso_str):
    """Converts any ISO timestamp to clean DD/MM/YYYY HH:MM in Spain local time."""
    if not iso_str:
        return ""
    try:
        dt = datetime.fromisoformat(iso_str)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        dt_spain = dt.astimezone(SPAIN_TZ)
        return dt_spain.strftime("%d/%m/%Y %H:%M")
    except Exception:
        return str(iso_str)[:16].replace("T", " ")


def _format_money(amount):
    if amount is None:
        return "0 €"
    if abs(amount) >= 1_000_000:
        return f"{amount / 1_000_000:.2f}M €"
    if abs(amount) >= 1_000:
        return f"{amount / 1_000:.1f}k €"
    return f"{amount:,} €".replace(",", ".")


def update_history_state(money, value):
    """Persists historical daily budget and team value snapshots for Chart.js (strictly 1 point per day)."""
    history_file = os.path.join(config.ROOT, ".state", "chart_history.json")
    os.makedirs(os.path.dirname(history_file), exist_ok=True)
    history = []
    if os.path.exists(history_file):
        try:
            with open(history_file, "r", encoding="utf-8") as f:
                history = json.load(f)
        except Exception:
            history = []
    
    today_label = datetime.now().strftime("%d/%m")
    today_date = datetime.now().strftime("%Y-%m-%d")
    entry = {
        "label": today_label,
        "date": today_date,
        "timestamp": int(datetime.now().timestamp()),
        "money": money or 0,
        "value": value or 0,
        "total": (money or 0) + (value or 0)
    }

    if history and (history[-1].get("label") == today_label or history[-1].get("date") == today_date):
        history[-1] = entry
    else:
        history.append(entry)
        
    history = history[-90:]
    try:
        with open(history_file, "w", encoding="utf-8") as f:
            json.dump(history, f, indent=2)
    except Exception:
        pass
    return history


def _format_kickoff(review_report=None):
    """Formats matchday info cleanly without raw ISOs or negative countdowns."""
    md = review_report.get("matchday", {}) if review_report else {}
    iso = md.get("kickoff")

    if not iso:
        try:
            from .sources import matchday
            from .api import FantasyClient
            fc = FantasyClient()
            iso = matchday.next_kickoff(fc)
        except Exception:
            try:
                from .sources import matchday
                iso = matchday.next_kickoff()
            except Exception:
                pass

    if not iso:
        return "Próxima Jornada", "Pendiente de fecha"

    try:
        dt = datetime.fromisoformat(iso)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        diff_sec = (dt - datetime.now(timezone.utc)).total_seconds()

        months_es = ["Ene", "Feb", "Mar", "Abr", "May", "Jun", "Jul", "Ago", "Sep", "Oct", "Nov", "Dic"]
        days_es = ["Lunes", "Martes", "Miércoles", "Jueves", "Viernes", "Sábado", "Domingo"]
        
        date_str = f"{days_es[dt.weekday()]} {dt.day} {months_es[dt.month - 1]} • {dt.strftime('%H:%M')}h"

        if diff_sec < 0:
            return date_str, "Jornada en curso"
        elif diff_sec < 86400:
            hours = int(diff_sec // 3600)
            return date_str, f"Comienza en {hours}h"
        else:
            d = diff_sec / 86400
            return date_str, f"En {d:.1f} días"
    except Exception:
        return "Próxima Jornada", "Pendiente de fecha"


def _format_markdown_report(text):
    """Renders full markdown response completely without truncating or cutting off text."""
    if not text:
        return "<p class='text-zinc-500 text-xs'>Sin informe disponible.</p>"
    
    lines = text.split("\n")
    out = []
    in_code = False
    code_buf = []
    
    for line in lines:
        stripped = line.strip()
        if stripped.startswith("```"):
            if in_code:
                code_str = html_lib.escape("\n".join(code_buf))
                out.append(f"<pre class='bg-zinc-950 border border-zinc-800/90 rounded-xl p-3 my-2 text-[11px] font-mono text-zinc-300 overflow-x-auto leading-relaxed'><code>{code_str}</code></pre>")
                code_buf = []
                in_code = False
            else:
                in_code = True
                code_buf = []
        elif in_code:
            code_buf.append(line)
        elif line.startswith("### "):
            out.append(f"<h3 class='text-xs uppercase tracking-wider font-bold text-zinc-200 mt-3 mb-1'>{html_lib.escape(line[4:])}</h3>")
        elif line.startswith("## "):
            out.append(f"<h2 class='text-sm font-semibold text-zinc-100 mt-3.5 mb-1'>{html_lib.escape(line[3:])}</h2>")
        elif line.startswith("# "):
            out.append(f"<h1 class='text-base font-bold text-zinc-100 mt-4 mb-1.5'>{html_lib.escape(line[2:])}</h1>")
        elif stripped.startswith(("* ", "- ", "• ")):
            content = stripped[2:]
            content = re.sub(r'\*\*(.*?)\*\*', r'<strong class="font-semibold text-zinc-200">\1</strong>', content)
            out.append(f"<li class='ml-4 list-disc text-xs text-zinc-300 my-0.5 leading-relaxed'>{content}</li>")
        elif re.match(r'^\d+\.\s', stripped):
            content = re.sub(r'^\d+\.\s', '', stripped)
            content = re.sub(r'\*\*(.*?)\*\*', r'<strong class="font-semibold text-zinc-200">\1</strong>', content)
            out.append(f"<p class='text-xs text-zinc-300 leading-relaxed my-1 pl-2 border-l border-zinc-800'>{content}</p>")
        elif stripped:
            content = re.sub(r'\*\*(.*?)\*\*', r'<strong class="font-semibold text-zinc-200">\1</strong>', line)
            out.append(f"<p class='text-xs text-zinc-300 leading-relaxed my-1'>{content}</p>")
    
    if in_code and code_buf:
        code_str = html_lib.escape("\n".join(code_buf))
        out.append(f"<pre class='bg-zinc-950 border border-zinc-800/90 rounded-xl p-3 my-2 text-[11px] font-mono text-zinc-300 overflow-x-auto leading-relaxed'><code>{code_str}</code></pre>")
    
    return "\n".join(out)


# Clean Vector SVG Icons Library
ICONS = {
    "wallet": '<svg class="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="1.8" d="M3 10h18M7 15h1m4 0h1m-7 4h12a3 3 0 003-3V8a3 3 0 00-3-3H6a3 3 0 00-3 3v8a3 3 0 003 3z"/></svg>',
    "shield": '<svg class="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="1.8" d="M9 12l2 2 4-4m5.618-4.016A11.955 11.955 0 0112 2.944a11.955 11.955 0 01-8.618 3.04A12.02 12.02 0 003 9c0 5.591 3.824 10.29 9 11.622 5.176-1.332 9-6.03 9-11.622 0-1.042-.133-2.052-.382-3.016z"/></svg>',
    "chart": '<svg class="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="1.8" d="M13 7h8m0 0v8m0-8l-8 8-4-4-6 6"/></svg>',
    "calendar": '<svg class="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="1.8" d="M8 7V3m8 4V3m-9 8h10M5 21h14a2 2 0 002-2V7a2 2 0 00-2-2H5a2 2 0 00-2 2v12a2 2 0 002 2z"/></svg>',
    "cpu": '<svg class="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="1.8" d="M9 3v2m6-2v2M9 19v2m6-2v2M3 9h2m-2 6h2m14-6h2m-2 6h2M7 19h10a2 2 0 002-2V7a2 2 0 00-2-2H7a2 2 0 00-2 2v10a2 2 0 002 2zM9 9h6v6H9V9z"/></svg>',
    "users": '<svg class="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="1.8" d="M12 4.354a4 4 0 110 5.292M15 21H3v-1a6 6 0 0112 0v1zm0 0h6v-1a6 6 0 00-9-5.197M13 7a4 4 0 11-8 0 4 4 0 018 0z"/></svg>',
    "market": '<svg class="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="1.8" d="M16 11V7a4 4 0 00-8 0v4M5 9h14l1 12H4L5 9z"/></svg>',
    "clock": '<svg class="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="1.8" d="M12 8v4l3 3m6-3a9 9 0 11-18 0 9 9 0 0118 0z"/></svg>',
    "history": '<svg class="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="1.8" d="M12 8v4l3 3m6-3a9 9 0 11-18 0 9 9 0 0118 0zM3.055 11H5a7.978 7.978 0 0115.89 0h1.945"/></svg>',
    "trophy": '<svg class="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="1.8" d="M5 3v4M3 5h4M6 17v4m-2-2h4m5-16l2.286 6.857L21 12l-5.714 2.143L13 21l-2.286-6.857L5 12l5.714-2.143L13 3z"/></svg>',
    "tag": '<svg class="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="1.8" d="M7 7h.01M7 3h5c.512 0 1.024.195 1.414.586l7 7a2 2 0 010 2.828l-7 7a2 2 0 01-2.828 0l-7-7A1.994 1.994 0 013 12V7a4 4 0 014-4z"/></svg>',
    "target": '<svg class="w-4 h-4 text-zinc-300" fill="none" stroke="currentColor" viewBox="0 0 24 24"><circle cx="12" cy="12" r="10" stroke-width="1.8"/><circle cx="12" cy="12" r="6" stroke-width="1.8"/><circle cx="12" cy="12" r="2" stroke-width="1.8"/></svg>',
    "search": '<svg class="w-3.5 h-3.5 text-zinc-500" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M21 21l-6-6m2-5a7 7 0 11-14 0 7 7 0 0114 0z"/></svg>',
    "zap": '<svg class="w-4 h-4 text-amber-400" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M13 10V3L4 14h7v7l9-11h-7z"/></svg>'
}


def generate_apple_dashboard(
    team, market, best_lineup, flips, gaps, review_report, gemini_response, decision, executed,
    prob_index=None, league_teams=None, my_received_offers=None, rival_clause_targets=None
):
    """Builds the comprehensive, mobile-friendly Apple dark mode HTML dashboard."""
    now = datetime.now()
    now_str = now.strftime("%d/%m/%Y a las %H:%M")
    
    money = team.get("teamMoney", 0)
    value = team.get("teamValue", 0)
    total_patrimony = money + value
    manager_name = (
        ((team.get("manager") or {}).get("managerName") if isinstance(team.get("manager"), dict) else None)
        or team.get("managerName")
        or team.get("teamName")
        or "Real Betis Frigopie"
    )
    total_points = team.get("teamPoints", 0)
    
    # Save & fetch chart history (Strictly 1 point per day)
    history = update_history_state(money, value)
    if len(history) < 2:
        history = [
            {"label": "Inicio", "money": money, "value": value, "total": total_patrimony},
            {"label": now.strftime("%d/%m"), "money": money, "value": value, "total": total_patrimony}
        ]

    pos_map = {1: "POR", 2: "DEF", 3: "MED", 4: "DEL", 5: "ENT"}
    pos_badge = {
        "POR": "bg-zinc-800/90 text-zinc-300 border-zinc-700/60",
        "DEF": "bg-zinc-800/90 text-zinc-300 border-zinc-700/60",
        "MED": "bg-zinc-800/90 text-zinc-300 border-zinc-700/60",
        "DEL": "bg-zinc-800/90 text-zinc-300 border-zinc-700/60",
        "ENT": "bg-zinc-800/90 text-zinc-300 border-zinc-700/60",
        "JUG": "bg-zinc-800/90 text-zinc-300 border-zinc-700/60"
    }

    # Load trends index for price variation and percentage
    from .sources.market_trends import trends_index
    t_index = trends_index()

    def _format_trend_badge(p_name):
        if not t_index:
            return '<span class="text-[10px] font-mono font-semibold text-zinc-500 bg-zinc-800/40 px-1.5 py-0.5 rounded border border-zinc-800">─ 0.0%</span>'
        from .matching import match_name
        t = match_name(p_name, p_name, t_index)
        if not t:
            return '<span class="text-[10px] font-mono font-semibold text-zinc-500 bg-zinc-800/40 px-1.5 py-0.5 rounded border border-zinc-800">─ 0.0%</span>'
        v_today = t.get("valor", 0)
        v_prev = t.get("valor1", 0)
        diff_val = v_today - v_prev
        diff_pct = (diff_val / v_prev) * 100.0 if v_prev > 0 else 0.0
        
        if diff_val > 0:
            return f'<span class="text-[10px] font-mono font-semibold text-emerald-400 bg-emerald-500/10 px-1.5 py-0.5 rounded border border-emerald-500/20">▲ +{diff_pct:.1f}% (+{_format_money(diff_val)}/d)</span>'
        elif diff_val < 0:
            return f'<span class="text-[10px] font-mono font-semibold text-rose-400 bg-rose-500/10 px-1.5 py-0.5 rounded border border-rose-500/20">▼ {diff_pct:.1f}% ({_format_money(diff_val)}/d)</span>'
        else:
            return '<span class="text-[10px] font-mono font-semibold text-zinc-400 bg-zinc-800/60 px-1.5 py-0.5 rounded border border-zinc-700/40">─ 0.0%</span>'

    # Extract market player IDs to mark owned players with 'En Mercado' badge
    market_player_ids = set()
    for m in (market or []):
        pm = m.get("playerMaster", {})
        if pm.get("id"):
            market_player_ids.add(str(pm.get("id")))

    # 1. TAB: SQUAD (Plantilla)
    players = team.get("players", [])
    squad_cards_html = ""
    for p in players:
        pm = p.get("playerMaster", {})
        pt = p.get("playerTeam", {})
        p_id = pm.get("id")
        p_name = pm.get("nickname") or pm.get("name") or "Desconocido"
        pos_id = pm.get("positionId")
        pos_str = pos_map.get(pos_id, "JUG")
        p_val = pt.get("marketValue") or pm.get("marketValue") or 0
        img = pm.get("images", {}).get("transparent", {}).get("256x256") or "https://assets-fantasy.llt-services.com/players/default.png"
        points = pm.get("points", 0)
        avg = pm.get("averagePoints", 0)
        status = pm.get("playerStatus", "ok")
        clause = p.get("buyoutClause") or pt.get("buyoutClause") or p_val

        # Real starting probability
        prob_str = "- %"
        if prob_index:
            from .matching import match_name
            minfo = match_name(pm.get("nickname", ""), pm.get("name", ""), prob_index)
            if minfo and minfo.get("prob") is not None:
                prob_str = f"{minfo.get('prob')}%"

        status_badge = '<span class="text-[10px] font-medium text-emerald-400 bg-emerald-500/10 px-2 py-0.5 rounded border border-emerald-500/20">Disponible</span>'
        if status in ("lesionado", "injured"):
            status_badge = '<span class="text-[10px] font-medium text-zinc-400 bg-zinc-800 px-2 py-0.5 rounded border border-zinc-700">Baja / Lesión</span>'
        elif status in ("sancionado", "suspended"):
            status_badge = '<span class="text-[10px] font-medium text-zinc-400 bg-zinc-800 px-2 py-0.5 rounded border border-zinc-700">Sancionado</span>'

        in_market = bool(p.get("playerMarket")) or (str(p_id) in market_player_ids)
        market_badge = '<span class="text-[10px] font-medium text-amber-400 bg-amber-500/10 px-2 py-0.5 rounded border border-amber-500/20">En Mercado</span>' if in_market else '<span class="text-[10px] text-zinc-500 bg-zinc-900 px-2 py-0.5 rounded border border-zinc-800">No listado</span>'
        trend_badge = _format_trend_badge(p_name)

        squad_cards_html += f"""
        <div class="bg-zinc-900/60 border border-zinc-800/80 rounded-xl p-3.5 hover:border-zinc-700 transition-all">
            <div class="flex items-start justify-between gap-1">
                <span class="px-2 py-0.5 text-[11px] font-mono font-semibold rounded border {pos_badge.get(pos_str, pos_badge['JUG'])}">{pos_str}</span>
                <div class="flex items-center space-x-1">
                    {market_badge}
                    {status_badge}
                </div>
            </div>
            <div class="flex items-center space-x-3 my-3">
                <img src="{img}" alt="{p_name}" class="w-11 h-11 rounded-lg object-cover bg-zinc-800/80 p-0.5 border border-zinc-800" onerror="this.src='https://assets-fantasy.llt-services.com/players/default.png'">
                <div class="min-w-0 flex-1">
                    <h4 class="font-semibold text-zinc-100 text-sm truncate">{p_name}</h4>
                    <p class="text-[11px] text-zinc-400 mt-0.5">Cláusula: {_format_money(int(clause))}</p>
                    <div class="flex items-center space-x-2 mt-1">
                        <span class="text-xs font-semibold text-zinc-200">{_format_money(p_val)}</span>
                        {trend_badge}
                    </div>
                </div>
            </div>
            <div class="grid grid-cols-3 gap-1.5 pt-2.5 border-t border-zinc-800/60 text-center text-xs">
                <div class="bg-zinc-950/60 rounded p-1.5">
                    <span class="text-zinc-500 block text-[9px] uppercase tracking-wider font-semibold">Puntos</span>
                    <span class="font-semibold text-zinc-200 text-xs">{points}</span>
                </div>
                <div class="bg-zinc-950/60 rounded p-1.5">
                    <span class="text-zinc-500 block text-[9px] uppercase tracking-wider font-semibold">Media</span>
                    <span class="font-semibold text-zinc-200 text-xs">{avg:.1f}</span>
                </div>
                <div class="bg-zinc-950/60 rounded p-1.5">
                    <span class="text-zinc-500 block text-[9px] uppercase tracking-wider font-semibold">Titular</span>
                    <span class="font-semibold text-emerald-400 text-xs">{prob_str}</span>
                </div>
            </div>
        </div>
        """

    # 1b. TAB: RECEIVED OFFERS (Ofertas Recibidas)
    offers_rows_html = ""
    if my_received_offers:
        for off in my_received_offers:
            j_name = off.get("jugador", "Desconocido")
            val_m = off.get("valor_mercado", 0)
            rec = off.get("oferta_recibida", 0)
            diff_pct = off.get("diferencia_pct", 0)
            buyer = off.get("comprador", "Sistema")
            pct_class = "text-emerald-400 bg-emerald-500/10 border-emerald-500/20" if diff_pct >= 0 else "text-rose-400 bg-rose-500/10 border-rose-500/20"

            offers_rows_html += f"""
            <tr class="border-b border-zinc-800/60 hover:bg-zinc-800/30 transition-colors">
                <td class="py-2.5 px-3 font-semibold text-zinc-200 text-xs">{j_name}</td>
                <td class="py-2.5 px-3 text-zinc-400 text-xs">{buyer}</td>
                <td class="py-2.5 px-3 text-right font-mono text-zinc-400 text-xs">{_format_money(val_m)}</td>
                <td class="py-2.5 px-3 text-right font-mono font-semibold text-zinc-100 text-xs">{_format_money(rec)}</td>
                <td class="py-2.5 px-3 text-right">
                    <span class="px-2 py-0.5 rounded text-[11px] font-mono font-semibold border {pct_class}">
                        {'+' if diff_pct > 0 else ''}{diff_pct:.1f}%
                    </span>
                </td>
            </tr>
            """
    else:
        offers_rows_html = """
        <tr>
            <td colspan="5" class="py-4 text-center text-zinc-500 text-xs">No hay ofertas entrantes pendientes en este momento.</td>
        </tr>
        """

    # 2. TAB: SYSTEM FREE AGENTS MARKET (Mercado Libre)
    market_rows_html = ""
    for m in (market or []):
        if m.get("discr") != "marketPlayerLeague":
            continue
        pm = m.get("playerMaster", {})
        pt = m.get("playerTeam", {})
        pos_id = pm.get("positionId")
        pos_str = pos_map.get(pos_id, "JUG")
        p_name = pm.get("nickname") or pm.get("name") or "Desconocido"
        sale_price = m.get("salePrice") or pt.get("marketValue") or pm.get("marketValue") or 0
        mid = m.get("id")
        img = pm.get("images", {}).get("transparent", {}).get("256x256") or "https://assets-fantasy.llt-services.com/players/default.png"
        bids_count = m.get("numberOfBids", 0)
        exp = m.get("expirationDate") or ""
        time_left_str = "Hoy"
        if exp:
            try:
                exp_dt = datetime.fromisoformat(exp)
                if exp_dt.tzinfo is None:
                    exp_dt = exp_dt.replace(tzinfo=timezone.utc)
                diff_sec = (exp_dt - datetime.now(timezone.utc)).total_seconds()
                if diff_sec > 3600:
                    time_left_str = f"{int(diff_sec // 3600)}h {int((diff_sec % 3600) // 60)}m"
                elif diff_sec > 60:
                    time_left_str = f"{int(diff_sec // 60)}m"
                elif diff_sec > 0:
                    time_left_str = f"{int(diff_sec)}s"
                else:
                    time_left_str = "Cerrado"
            except Exception:
                pass

        prob_str = "- %"
        if prob_index:
            from .matching import match_name
            minfo = match_name(pm.get("nickname", ""), pm.get("name", ""), prob_index)
            if minfo and minfo.get("prob") is not None:
                prob_str = f"{minfo.get('prob')}%"

        bids_badge = f'<span class="text-xs text-zinc-400">{bids_count}</span>'
        if bids_count > 0:
            bids_badge = f'<span class="text-xs font-semibold text-zinc-100 bg-zinc-800 px-2 py-0.5 rounded border border-zinc-700">{bids_count} puja(s)</span>'

        trend_badge = _format_trend_badge(p_name)

        market_rows_html += f"""
        <tr class="market-row border-b border-zinc-800/60 hover:bg-zinc-800/30 transition-colors" data-pos="{pos_str}" data-name="{p_name.lower()}">
            <td class="py-2.5 px-3">
                <div class="flex items-center space-x-2.5">
                    <img src="{img}" class="w-8 h-8 rounded bg-zinc-800 object-cover border border-zinc-700/50" onerror="this.src='https://assets-fantasy.llt-services.com/players/default.png'">
                    <div>
                        <div class="font-semibold text-zinc-200 text-xs">{p_name}</div>
                        <div class="text-[10px] text-zinc-500 font-mono">#{mid}</div>
                    </div>
                </div>
            </td>
            <td class="py-2.5 px-3">
                <span class="px-1.5 py-0.5 rounded text-[10px] font-mono font-medium border {pos_badge.get(pos_str, pos_badge['JUG'])}">{pos_str}</span>
            </td>
            <td class="py-2.5 px-3 text-right font-semibold text-zinc-200 text-xs font-mono">{_format_money(sale_price)}</td>
            <td class="py-2.5 px-3 text-center">{trend_badge}</td>
            <td class="py-2.5 px-3 text-center text-xs font-mono font-medium text-emerald-400">{prob_str}</td>
            <td class="py-2.5 px-3 text-center">{bids_badge}</td>
            <td class="py-2.5 px-3 text-right text-[11px] text-zinc-400 font-mono">{time_left_str}</td>
        </tr>
        """

    # 3. TAB: LEAGUE RANKING & RIVAL BUYOUT CLAUSES (Liga & Clausulazos)
    league_table_html = ""
    if league_teams:
        sorted_teams = sorted(league_teams, key=lambda x: -(x.get("teamPoints") or 0))
        for idx, lt in enumerate(sorted_teams, start=1):
            t_name = lt.get("manager", {}).get("managerName") or lt.get("teamName") or f"Equipo {idx}"
            t_pts = lt.get("teamPoints", 0)
            t_val = lt.get("teamValue", 0)
            is_me = str(lt.get("id")) == str(team.get("id"))
            row_bg = "bg-zinc-800/40 font-semibold" if is_me else "hover:bg-zinc-800/20"
            tag_me = ' <span class="text-[9px] text-emerald-400 bg-emerald-500/10 px-1 py-0.2 rounded border border-emerald-500/20 ml-1">Tú</span>' if is_me else ''

            league_table_html += f"""
            <tr class="border-b border-zinc-800/60 {row_bg} transition-colors">
                <td class="py-2.5 px-3 text-center font-mono text-zinc-400 text-xs">#{idx}</td>
                <td class="py-2.5 px-3 font-semibold text-zinc-200 text-xs">{t_name}{tag_me}</td>
                <td class="py-2.5 px-3 text-right font-mono font-semibold text-zinc-100 text-xs">{t_pts} pts</td>
                <td class="py-2.5 px-3 text-right font-mono text-zinc-400 text-xs">{_format_money(t_val)}</td>
            </tr>
            """

    # 3b. RIVAL BUYOUT CLAUSES RADAR
    # Calculate exact countdowns for all rival players from league_teams
    if league_teams:
        rival_clause_targets = []
        now_utc = datetime.now(timezone.utc)
        for lt in league_teams:
            if str(lt.get("id")) == str(team.get("id")):
                continue
            rival_mgr_name = lt.get("manager", {}).get("managerName") or lt.get("teamName") or "Rival"
            for p in lt.get("players", []):
                pm = p.get("playerMaster", {})
                p_id = pm.get("id")
                name = pm.get("nickname") or pm.get("name") or "Desconocido"
                pos_id = pm.get("positionId")
                pos_str = pos_map.get(pos_id, "JUG")
                val = pm.get("marketValue") or 0
                clause = p.get("buyoutClause") or p.get("playerTeam", {}).get("buyoutClause") or val
                locked_until = p.get("buyoutClauseLockedEndTime")
                
                is_open = True
                shield_status = "Abierta"
                seconds_to_open = 0
                if locked_until:
                    try:
                        exp_dt = datetime.fromisoformat(locked_until)
                        if exp_dt.tzinfo is None:
                            exp_dt = exp_dt.replace(tzinfo=timezone.utc)
                        diff = (exp_dt - now_utc).total_seconds()
                        if diff > 0:
                            is_open = False
                            seconds_to_open = int(diff)
                            hours = int(diff // 3600)
                            mins = int((diff % 3600) // 60)
                            if hours >= 24:
                                d = diff / 86400
                                shield_status = f"En {d:.1f} días ({exp_dt.astimezone(SPAIN_TZ).strftime('%d/%m %H:%M')})"
                            else:
                                shield_status = f"En {hours}h {mins}m ({exp_dt.astimezone(SPAIN_TZ).strftime('%H:%M')})"
                    except Exception:
                        pass

                prob = None
                if prob_index:
                    from .matching import match_name
                    minfo = match_name(pm.get("nickname", ""), pm.get("name", ""), prob_index)
                    if minfo and minfo.get("prob") is not None:
                        prob = minfo.get("prob")

                rival_clause_targets.append({
                    "playerId": p_id,
                    "nombre": name,
                    "posicion": pos_str,
                    "equipo_rival": rival_mgr_name,
                    "valor_mercado": val,
                    "clausula": int(clause),
                    "ratio_clausula_valor": round(clause / val, 2) if val else 0,
                    "clausula_abierta": is_open,
                    "segundos_para_abrir": seconds_to_open,
                    "estado_escudo": shield_status,
                    "prob_titular": prob
                })

    rival_clauses_html = ""
    if rival_clause_targets:
        # Sort by open first, then ratio
        sorted_targets = sorted(rival_clause_targets, key=lambda x: (not x.get("clausula_abierta"), x.get("ratio_clausula_valor", 99)))
        for rc in sorted_targets:
            p_name = rc.get("nombre", "Desconocido")
            pos_str = rc.get("posicion", "JUG")
            rival_team = rc.get("equipo_rival", "Rival")
            val_m = rc.get("valor_mercado", 0)
            clause_amt = rc.get("clausula", 0)
            is_open = rc.get("clausula_abierta", False)
            shield_status = rc.get("estado_escudo", "Abierta")
            prob = rc.get("prob_titular")
            prob_str = f"{prob}%" if prob is not None else "- %"

            status_tag = '<span class="text-[10px] font-semibold text-emerald-400 bg-emerald-500/10 px-2 py-0.5 rounded border border-emerald-500/20">Abierta (Comprar ya)</span>' if is_open else f'<span class="text-[10px] text-zinc-400 bg-zinc-800 px-2 py-0.5 rounded border border-zinc-700 font-mono">Blindada ({shield_status})</span>'
            trend_badge = _format_trend_badge(p_name)

            rival_clauses_html += f"""
            <tr class="border-b border-zinc-800/60 hover:bg-zinc-800/30 transition-colors">
                <td class="py-2.5 px-3">
                    <div class="font-semibold text-zinc-200 text-xs">{p_name}</div>
                    <div class="text-[10px] text-zinc-500">{rival_team}</div>
                </td>
                <td class="py-2.5 px-3">
                    <span class="px-1.5 py-0.5 rounded text-[10px] font-mono font-medium border {pos_badge.get(pos_str, pos_badge['JUG'])}">{pos_str}</span>
                </td>
                <td class="py-2.5 px-3 text-right font-mono text-zinc-400 text-xs">{_format_money(val_m)}</td>
                <td class="py-2.5 px-3 text-center">{trend_badge}</td>
                <td class="py-2.5 px-3 text-right font-mono font-semibold text-amber-400 text-xs">{_format_money(clause_amt)}</td>
                <td class="py-2.5 px-3 text-center text-xs font-mono font-medium text-emerald-400">{prob_str}</td>
                <td class="py-2.5 px-3 text-right">{status_tag}</td>
            </tr>
            """
    else:
        rival_clauses_html = """
        <tr>
            <td colspan="6" class="py-4 text-center text-zinc-500 text-xs">Cargando datos de jugadores rivales...</td>
        </tr>
        """

    # 4. TAB: GAMEWEEK POINTS CHART
    gw_labels = ["J1", "J2", "J3", "J4"]
    gw_points = [0, 0, 0, 0]

    # 5. TAB: SCHEDULED ACTIONS & SNIPING PLAN
    scheduled_bids = state.load_bid_plan()
    scheduled_buyouts = ((decision or {}).get("clausulazos_programados") or [])
    scheduled_reminders = state.load_reminders()

    dinero_pujas = sum(int(b.get("max_bid", 0)) for b in scheduled_bids)
    dinero_clausulas = sum(int(c.get("clausula", 0)) for c in scheduled_buyouts)
    total_comprometido = dinero_pujas + dinero_clausulas
    presupuesto_proyectado = money - total_comprometido

    scheduled_items = []

    # Bids planned for last-minute close
    for b in scheduled_bids:
        m_id = b.get("market_id")
        max_b = b.get("max_bid")
        n = b.get("nombre", f"Jugador #{m_id}")
        scheduled_items.append({
            "icon": ICONS['market'],
            "tipo": "Puja de Último Minuto Programada",
            "titulo": f"Puja tope por {n}: {_format_money(max_b)}",
            "hora": "22:10 - 22:18 (Hora España)",
            "badge": "Mercado",
            "badge_class": "bg-emerald-500/10 text-emerald-400 border-emerald-500/20"
        })

    # Scheduled Buyouts
    for c in scheduled_buyouts:
        p_name = c.get("nombre", "Jugador")
        cl = c.get("clausula", 0)
        ap = c.get("apertura_iso", "")
        ap_str = _format_spain_time(ap) if ap else "Próxima apertura"
        scheduled_items.append({
            "icon": ICONS['zap'],
            "tipo": "Clausulazo Programado",
            "titulo": f"Compra por cláusula de {p_name}: {_format_money(cl)}",
            "hora": f"Al abrir escudo: {ap_str}",
            "badge": "Clausulazo",
            "badge_class": "bg-amber-500/10 text-amber-400 border-amber-500/20"
        })

    # Reminders
    for r in scheduled_reminders:
        scheduled_items.append({
            "icon": ICONS['clock'],
            "tipo": "Recordatorio Programado",
            "titulo": r.get("message", "Alarma"),
            "hora": _format_spain_time(r.get("fire_at")),
            "badge": "Recordatorio",
            "badge_class": "bg-zinc-800 text-zinc-300 border-zinc-700"
        })

    budget_summary_bar = f"""
    <div class="grid grid-cols-3 gap-2 p-3 bg-zinc-950/60 border border-zinc-800/80 rounded-xl mb-3 text-center text-xs font-mono">
        <div>
            <div class="text-[10px] text-zinc-500 uppercase font-semibold">Caja Actual</div>
            <div class="font-bold text-zinc-200 text-xs sm:text-sm mt-0.5">{_format_money(money)}</div>
        </div>
        <div>
            <div class="text-[10px] text-amber-400 uppercase font-semibold">En Plan</div>
            <div class="font-bold text-amber-400 text-xs sm:text-sm mt-0.5">-{_format_money(total_comprometido)}</div>
        </div>
        <div>
            <div class="text-[10px] text-emerald-400 uppercase font-semibold">Proyectado</div>
            <div class="font-bold text-emerald-400 text-xs sm:text-sm mt-0.5">{_format_money(presupuesto_proyectado)}</div>
        </div>
    </div>
    """

    scheduled_actions_html = ""
    if scheduled_items:
        scheduled_actions_html += budget_summary_bar
        for s in scheduled_items:
            scheduled_actions_html += f"""
            <div class="flex items-center justify-between p-3 bg-zinc-900/60 border border-zinc-800/80 rounded-xl hover:border-zinc-700 transition-all mb-2">
                <div class="flex items-center space-x-3 min-w-0">
                    <span class="p-1.5 bg-zinc-800 rounded-lg border border-zinc-700 text-zinc-300 flex-shrink-0">{s['icon']}</span>
                    <div class="min-w-0">
                        <div class="font-medium text-zinc-200 text-xs truncate">{s['titulo']}</div>
                        <div class="text-[10px] text-zinc-500 font-mono mt-0.5">{s['tipo']} • {s['hora']}</div>
                    </div>
                </div>
                <span class="px-2 py-0.5 rounded text-[10px] font-mono font-semibold border {s['badge_class']} flex-shrink-0 ml-2">
                    {s['badge']}
                </span>
            </div>
            """
    else:
        scheduled_actions_html = """
        <div class="p-4 text-center text-zinc-500 text-xs bg-zinc-900/30 border border-zinc-800/60 rounded-xl">
            No hay acciones programadas pendientes en este momento. Las nuevas compras de último minuto y clausulazos se programarán en el pase de las 17:00 / 22:10.
        </div>
        """

    # 5b. TAB: HISTORICAL REASONINGS ARCHIVE
    history_file = os.path.join(config.ROOT, ".state", "reasoning_history.json")
    r_history = []
    if os.path.exists(history_file):
        try:
            with open(history_file, "r", encoding="utf-8") as f:
                r_history = json.load(f)
        except Exception:
            r_history = []

    reasoning_archive_html = ""
    if r_history:
        for idx, item in enumerate(reversed(r_history)):
            ts = _format_spain_time(item.get("timestamp") or item.get("date_str"))
            resp = item.get("response", "")
            fmt_resp = _format_markdown_report(resp)
            is_first = (idx == 0)

            reasoning_archive_html += f"""
            <details class="group bg-zinc-900/50 hover:bg-zinc-900/80 border border-zinc-800/80 rounded-xl transition-all overflow-hidden mb-2" {'open' if is_first else ''}>
                <summary class="flex items-center justify-between p-3 cursor-pointer list-none select-none">
                    <div class="flex items-center space-x-2.5">
                        <span class="p-1.5 bg-zinc-800 rounded-lg border border-zinc-700 text-zinc-300">{ICONS['cpu']}</span>
                        <div>
                            <span class="font-semibold text-zinc-200 text-xs">Informe Táctico Gemini</span>
                            <span class="text-[10px] text-zinc-500 font-mono ml-2">{ts} (Hora España)</span>
                        </div>
                    </div>
                    <span class="text-zinc-500 text-xs font-mono group-open:rotate-180 transition-transform">▼</span>
                </summary>
                <div class="px-4 pb-4 pt-2 border-t border-zinc-800/60 bg-zinc-950/40 text-xs text-zinc-300 space-y-2">
                    {fmt_resp}
                </div>
            </details>
            """
    else:
        reasoning_archive_html = f"""
        <div class="bg-zinc-900/50 border border-zinc-800/80 rounded-xl p-4 text-xs text-zinc-300 space-y-2">
            {_format_markdown_report(gemini_response)}
        </div>
        """

    # 5c. ACTION AUDIT TIMELINE
    recent_events = events.load(limit=30)
    timeline_html = ""
    for ev in reversed(recent_events):
        k = ev.get("kind", "note")
        title = ev.get("title", "")
        ts = _format_spain_time(ev.get("iso"))
        detail_obj = ev.get("detail")
        detail_json = ""
        if detail_obj:
            try:
                detail_json = json.dumps(detail_obj, ensure_ascii=False, indent=2)
            except Exception:
                detail_json = str(detail_obj)

        timeline_html += f"""
        <details class="group bg-zinc-900/50 hover:bg-zinc-900/80 border border-zinc-800/80 rounded-xl transition-all overflow-hidden mb-2">
            <summary class="flex items-center justify-between p-3 cursor-pointer list-none select-none">
                <div class="flex items-center space-x-3 min-w-0">
                    <span class="p-1.5 bg-zinc-800 rounded-lg border border-zinc-700 text-zinc-300 flex-shrink-0">{ICONS['clock']}</span>
                    <div class="min-w-0">
                        <div class="font-medium text-zinc-200 text-xs truncate">{title}</div>
                        <div class="text-[10px] text-zinc-500 font-mono mt-0.5 uppercase tracking-wider">{k} • {ts}</div>
                    </div>
                </div>
                <span class="text-zinc-500 text-xs font-mono group-open:rotate-180 transition-transform">▼</span>
            </summary>
            <div class="px-3.5 pb-3.5 pt-2 border-t border-zinc-800/60 bg-zinc-950/40 text-xs text-zinc-400 space-y-2">
                <div class="flex items-center justify-between text-[11px] font-mono text-zinc-500">
                    <span>ID Ejecución: {ev.get('run', '-')}</span>
                    <span>Estado: {ev.get('status', 'ok')}</span>
                </div>
                {f'<pre class="bg-zinc-950 border border-zinc-800/80 rounded-lg p-2.5 text-[11px] font-mono text-emerald-400 overflow-x-auto"><code>{html_lib.escape(detail_json)}</code></pre>' if detail_json else '<p class="text-[11px] text-zinc-500">Sin datos adicionales.</p>'}
            </div>
        </details>
        """

    # Matchday formatting
    kickoff_text, countdown_text = _format_kickoff(review_report)

    # Chart.js JSON data
    labels_json = json.dumps([h["label"] for h in history])
    money_json = json.dumps([h["money"] for h in history])
    value_json = json.dumps([h["value"] for h in history])
    total_json = json.dumps([h["total"] for h in history])

    gw_labels_json = json.dumps(gw_labels)
    gw_points_json = json.dumps(gw_points)

    # Render Complete Mobile-Friendly Apple Minimalist Dashboard
    html_content = f"""<!DOCTYPE html>
<html lang="es" class="dark">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0, maximum-scale=1.0, user-scalable=no">
    <title>FantasyBot OS • {manager_name}</title>
    <script src="https://cdn.tailwindcss.com"></script>
    <script src="https://cdn.jsdelivr.net/npm/chart.js"></script>
    <link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&family=JetBrains+Mono:wght@400;500;600&display=swap" rel="stylesheet">
    <style>
        body {{
            background-color: #09090b;
            color: #f4f4f5;
            font-family: 'Inter', -apple-system, BlinkMacSystemFont, sans-serif;
            -webkit-tap-highlight-color: transparent;
        }}
        .font-mono {{
            font-family: 'JetBrains Mono', monospace;
        }}
        .tab-active {{
            background: #27272a;
            color: #fafafa;
            border-color: #3f3f46;
        }}
        ::-webkit-scrollbar {{
            width: 4px;
            height: 4px;
        }}
        ::-webkit-scrollbar-track {{
            background: #09090b;
        }}
        ::-webkit-scrollbar-thumb {{
            background: #27272a;
            border-radius: 4px;
        }}
        details > summary::-webkit-details-marker {{
            display: none;
        }}
        .no-scrollbar::-webkit-scrollbar {{
            display: none;
        }}
        .no-scrollbar {{
            -ms-overflow-style: none;
            scrollbar-width: none;
        }}
    </style>
</head>
<body class="min-h-screen antialiased selection:bg-zinc-800 selection:text-white pb-12">
    <!-- Compact Apple Header -->
    <header class="border-b border-zinc-800/80 bg-zinc-950/90 backdrop-blur sticky top-0 z-50 px-3 py-2.5 sm:px-4 sm:py-3">
        <div class="max-w-6xl mx-auto flex items-center justify-between">
            <div class="flex items-center space-x-2">
                <div class="w-6 h-6 rounded bg-zinc-800 border border-zinc-700 flex items-center justify-center text-zinc-300 flex-shrink-0">
                    {ICONS['shield']}
                </div>
                <div class="min-w-0">
                    <h1 class="font-semibold text-zinc-100 text-xs sm:text-sm tracking-tight truncate">{manager_name}</h1>
                </div>
            </div>

            <div class="flex items-center space-x-2">
                <div class="flex items-center space-x-1 text-[11px] text-zinc-400 font-mono">
                    <span class="w-1.5 h-1.5 rounded-full bg-emerald-400 animate-pulse"></span>
                    <span>17:00 / 22:10</span>
                </div>
                <span class="text-[10px] font-mono px-1.5 py-0.5 rounded border {('bg-emerald-500/10 text-emerald-400 border-emerald-500/20' if executed else 'bg-zinc-800 text-zinc-400 border-zinc-700')}">
                    {'Ejecutado' if executed else 'Análisis'}
                </span>
            </div>
        </div>
    </header>

    <!-- Main Container -->
    <main class="max-w-6xl mx-auto px-3 sm:px-4 py-4 space-y-4">
        
        <!-- Metrics Bento Grid (4 Compact Cards) -->
        <section class="grid grid-cols-2 lg:grid-cols-4 gap-2.5 sm:gap-3">
            <!-- Box 1: Presupuesto -->
            <div class="bg-zinc-900/60 border border-zinc-800/80 rounded-xl p-3 sm:p-3.5">
                <div class="flex items-center justify-between text-zinc-400 text-[10px] sm:text-[11px] uppercase tracking-wider font-semibold">
                    <span>Presupuesto</span>
                    <span class="text-zinc-400">{ICONS['wallet']}</span>
                </div>
                <div class="mt-1">
                    <div class="text-lg sm:text-xl font-bold text-zinc-100 font-mono tracking-tight">{_format_money(money)}</div>
                    {f'<p class="text-[10px] sm:text-[11px] text-amber-400 font-mono mt-0.5 font-medium">Proy: {_format_money(presupuesto_proyectado)} (-{_format_money(total_comprometido)})</p>' if total_comprometido > 0 else '<p class="text-[10px] sm:text-[11px] text-zinc-500 mt-0.5">En caja disponible</p>'}
                </div>
            </div>

            <!-- Box 2: Valor Plantilla -->
            <div class="bg-zinc-900/60 border border-zinc-800/80 rounded-xl p-3 sm:p-3.5">
                <div class="flex items-center justify-between text-zinc-400 text-[10px] sm:text-[11px] uppercase tracking-wider font-semibold">
                    <span>Plantilla</span>
                    <span class="text-zinc-400">{ICONS['users']}</span>
                </div>
                <div class="mt-1">
                    <div class="text-lg sm:text-xl font-bold text-zinc-100 font-mono tracking-tight">{_format_money(value)}</div>
                    <p class="text-[10px] sm:text-[11px] text-zinc-500 mt-0.5">{len(players)} futbolistas</p>
                </div>
            </div>

            <!-- Box 3: Total Club -->
            <div class="bg-zinc-900/60 border border-zinc-800/80 rounded-xl p-3 sm:p-3.5">
                <div class="flex items-center justify-between text-zinc-400 text-[10px] sm:text-[11px] uppercase tracking-wider font-semibold">
                    <span>Patrimonio Total</span>
                    <span class="text-zinc-400">{ICONS['chart']}</span>
                </div>
                <div class="mt-1">
                    <div class="text-lg sm:text-xl font-bold text-zinc-100 font-mono tracking-tight">{_format_money(total_patrimony)}</div>
                    <p class="text-[10px] sm:text-[11px] text-zinc-500 mt-0.5">Activos totales</p>
                </div>
            </div>

            <!-- Box 4: Próxima Jornada -->
            <div class="bg-zinc-900/60 border border-zinc-800/80 rounded-xl p-3 sm:p-3.5">
                <div class="flex items-center justify-between text-zinc-400 text-[10px] sm:text-[11px] uppercase tracking-wider font-semibold">
                    <span>Próxima Jornada</span>
                    <span class="text-zinc-400">{ICONS['calendar']}</span>
                </div>
                <div class="mt-1">
                    <div class="text-xs font-semibold text-zinc-200 truncate">{kickoff_text}</div>
                    <p class="text-[10px] sm:text-[11px] text-emerald-400 font-mono mt-0.5 font-medium">{countdown_text}</p>
                </div>
            </div>
        </section>

        <!-- Financial Evolution Chart -->
        <section class="bg-zinc-900/60 border border-zinc-800/80 rounded-xl p-3.5 sm:p-4">
            <div class="flex flex-col sm:flex-row sm:items-center justify-between gap-1.5 pb-2.5 border-b border-zinc-800/60">
                <div>
                    <h2 class="text-xs uppercase tracking-wider font-bold text-zinc-300">Evolución Financiera</h2>
                    <p class="text-[10px] sm:text-[11px] text-zinc-500">Historial diario de presupuesto y valor de plantilla</p>
                </div>
                <div class="flex items-center space-x-3 text-[10px] sm:text-[11px] font-mono text-zinc-400">
                    <span class="flex items-center space-x-1"><span class="w-2 h-2 rounded-full bg-zinc-100"></span><span>Total</span></span>
                    <span class="flex items-center space-x-1"><span class="w-2 h-2 rounded-full bg-emerald-400"></span><span>Caja</span></span>
                    <span class="flex items-center space-x-1"><span class="w-2 h-2 rounded-full bg-zinc-500"></span><span>Plantilla</span></span>
                </div>
            </div>
            <div class="h-48 sm:h-52 mt-2">
                <canvas id="patrimonyChart"></canvas>
            </div>
        </section>

        <!-- Mobile-Friendly Horizontal Pill Navigation Tabs -->
        <nav class="pt-2 pb-1 border-b border-zinc-800/80 sticky top-12 z-40 bg-zinc-950/95 backdrop-blur -mx-3 px-3 sm:mx-0 sm:px-0">
            <div class="flex items-center space-x-1.5 overflow-x-auto no-scrollbar pb-1 text-xs font-medium">
                <button onclick="switchTab('tab-squad')" id="btn-tab-squad" class="tab-btn tab-active px-3 py-1.5 rounded-lg border border-transparent transition-all flex items-center space-x-1.5 flex-shrink-0">
                    <span>{ICONS['users']}</span>
                    <span>Plantilla ({len(players)})</span>
                </button>
                <button onclick="switchTab('tab-market')" id="btn-tab-market" class="tab-btn px-3 py-1.5 rounded-lg text-zinc-400 hover:text-zinc-200 border border-transparent transition-all flex items-center space-x-1.5 flex-shrink-0">
                    <span>{ICONS['market']}</span>
                    <span>Mercado Libre</span>
                </button>
                <button onclick="switchTab('tab-rivals')" id="btn-tab-rivals" class="tab-btn px-3 py-1.5 rounded-lg text-zinc-400 hover:text-zinc-200 border border-transparent transition-all flex items-center space-x-1.5 flex-shrink-0">
                    <span>{ICONS['zap']}</span>
                    <span>Rivales & Cláusulas</span>
                </button>
                <button onclick="switchTab('tab-points')" id="btn-tab-points" class="tab-btn px-3 py-1.5 rounded-lg text-zinc-400 hover:text-zinc-200 border border-transparent transition-all flex items-center space-x-1.5 flex-shrink-0">
                    <span>{ICONS['trophy']}</span>
                    <span>Puntos ({total_points})</span>
                </button>
                <button onclick="switchTab('tab-history')" id="btn-tab-history" class="tab-btn px-3 py-1.5 rounded-lg text-zinc-400 hover:text-zinc-200 border border-transparent transition-all flex items-center space-x-1.5 flex-shrink-0">
                    <span>{ICONS['history']}</span>
                    <span>Historial</span>
                </button>
            </div>
        </nav>

        <!-- TAB CONTENT CONTAINER -->
        <div class="pt-2">
            
            <!-- TAB 1: SQUAD & RECEIVED OFFERS -->
            <section id="tab-squad" class="tab-content space-y-4">
                <!-- Squad Grid -->
                <div>
                    <div class="flex items-center justify-between pb-2">
                        <span class="text-xs text-zinc-400">Todos los futbolistas se listan automáticamente para capturar ofertas diarias.</span>
                        <span class="text-[11px] text-zinc-500 font-mono">Huecos: {', '.join(gaps) if gaps else 'Cubiertos'}</span>
                    </div>
                    <div class="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-2.5 sm:gap-3">
                        {squad_cards_html or '<div class="col-span-4 p-6 text-center text-zinc-500 text-xs">Plantilla vacía</div>'}
                    </div>
                </div>

                <!-- Received Offers Table -->
                <div class="bg-zinc-900/60 border border-zinc-800/80 rounded-xl p-3.5 sm:p-4 mt-4">
                    <div class="flex items-center justify-between pb-2.5 border-b border-zinc-800/60">
                        <div class="flex items-center space-x-2">
                            <span class="text-zinc-400">{ICONS['tag']}</span>
                            <h3 class="text-xs uppercase font-bold text-zinc-200">Ofertas Recibidas por mis Jugadores</h3>
                        </div>
                        <span class="text-[10px] text-zinc-500 font-mono">{len(my_received_offers or [])} activa(s)</span>
                    </div>
                    <div class="overflow-x-auto mt-2">
                        <table class="w-full text-left text-xs">
                            <thead>
                                <tr class="text-zinc-400 border-b border-zinc-800/80 bg-zinc-950/40 text-[10px] uppercase font-mono tracking-wider">
                                    <th class="py-2.5 px-3">Futbolista</th>
                                    <th class="py-2.5 px-3">Comprador</th>
                                    <th class="py-2.5 px-3 text-right">Valor Mercado</th>
                                    <th class="py-2.5 px-3 text-right">Oferta Recibida</th>
                                    <th class="py-2.5 px-3 text-right">Diferencia</th>
                                </tr>
                            </thead>
                            <tbody>
                                {offers_rows_html}
                            </tbody>
                        </table>
                    </div>
                </div>
            </section>

            <!-- TAB 2: SYSTEM FREE AGENTS MARKET -->
            <section id="tab-market" class="tab-content hidden space-y-3">
                <div class="flex flex-col sm:flex-row sm:items-center justify-between gap-2 pb-1">
                    <span class="text-xs text-zinc-400">Jugadores libres sacados por el sistema de la liga.</span>
                    <div class="flex items-center space-x-2">
                        <div class="relative flex-1 sm:flex-none">
                            <span class="absolute inset-y-0 left-0 flex items-center pl-2.5 pointer-events-none">{ICONS['search']}</span>
                            <input type="text" id="marketSearch" onkeyup="filterMarket()" placeholder="Buscar jugador..." class="bg-zinc-900 border border-zinc-800 text-zinc-200 text-xs pl-7 pr-3 py-1.5 rounded-lg focus:outline-none focus:border-zinc-700 w-full sm:w-44">
                        </div>
                        <select id="posFilter" onchange="filterMarket()" class="bg-zinc-900 border border-zinc-800 text-zinc-300 text-xs px-2 py-1.5 rounded-lg focus:outline-none focus:border-zinc-700">
                            <option value="ALL">Todas pos.</option>
                            <option value="POR">POR</option>
                            <option value="DEF">DEF</option>
                            <option value="MED">MED</option>
                            <option value="DEL">DEL</option>
                        </select>
                    </div>
                </div>

                <div class="bg-zinc-900/60 border border-zinc-800/80 rounded-xl overflow-hidden">
                    <div class="overflow-x-auto">
                        <table class="w-full text-left text-xs" id="marketTable">
                            <thead>
                                <tr class="text-zinc-400 border-b border-zinc-800/80 bg-zinc-950/40 text-[10px] uppercase font-mono tracking-wider">
                                    <th class="py-2.5 px-3">Jugador</th>
                                    <th class="py-2.5 px-3">Pos</th>
                                    <th class="py-2.5 px-3 text-right">Precio Salida</th>
                                    <th class="py-2.5 px-3 text-center">Tendencia</th>
                                    <th class="py-2.5 px-3 text-center">Titular</th>
                                    <th class="py-2.5 px-3 text-center">Pujas</th>
                                    <th class="py-2.5 px-3 text-right">Cierre</th>
                                </tr>
                            </thead>
                            <tbody>
                                {market_rows_html}
                            </tbody>
                        </table>
                    </div>
                </div>
            </section>

            <!-- TAB 3: LEAGUE RANKING & RIVAL BUYOUT CLAUSES RADAR -->
            <section id="tab-rivals" class="tab-content hidden space-y-4">
                <!-- League Classification Table -->
                <div class="bg-zinc-900/60 border border-zinc-800/80 rounded-xl p-3.5 sm:p-4">
                    <div class="flex items-center justify-between pb-2.5 border-b border-zinc-800/60">
                        <div class="flex items-center space-x-2">
                            <span class="text-zinc-400">{ICONS['trophy']}</span>
                            <h3 class="text-xs uppercase font-bold text-zinc-200">Clasificación de la Liga</h3>
                        </div>
                        <span class="text-[10px] text-zinc-500 font-mono">{len(league_teams or [])} equipos</span>
                    </div>
                    <div class="overflow-x-auto mt-2">
                        <table class="w-full text-left text-xs">
                            <thead>
                                <tr class="text-zinc-400 border-b border-zinc-800/80 bg-zinc-950/40 text-[10px] uppercase font-mono tracking-wider">
                                    <th class="py-2.5 px-3 text-center">Pos</th>
                                    <th class="py-2.5 px-3">Mánager / Equipo</th>
                                    <th class="py-2.5 px-3 text-right">Puntos</th>
                                    <th class="py-2.5 px-3 text-right">Valor Plantilla</th>
                                </tr>
                            </thead>
                            <tbody>
                                {league_table_html}
                            </tbody>
                        </table>
                    </div>
                </div>

                <!-- Rival Buyout Clauses Radar -->
                <div class="bg-zinc-900/60 border border-zinc-800/80 rounded-xl p-3.5 sm:p-4">
                    <div class="flex items-center justify-between pb-2.5 border-b border-zinc-800/60">
                        <div class="flex items-center space-x-2">
                            <span class="text-zinc-400">{ICONS['zap']}</span>
                            <h3 class="text-xs uppercase font-bold text-zinc-200">Radar de Clausulazos a Rivales</h3>
                        </div>
                        <span class="text-[10px] text-zinc-500 font-mono">Cláusulas abiertas y escudos</span>
                    </div>
                    <p class="text-[11px] text-zinc-400 mt-2 mb-3 leading-relaxed">
                        Lista completa de futbolistas de rivales. Si una cláusula está abierta y es rentable, la IA la ejecuta directamente. Si está blindada por el escudo de 14 días, se muestra el tiempo exacto restante para su apertura.
                    </p>
                    <div class="overflow-x-auto">
                        <table class="w-full text-left text-xs">
                            <thead>
                                <tr class="text-zinc-400 border-b border-zinc-800/80 bg-zinc-950/40 text-[10px] uppercase font-mono tracking-wider">
                                    <th class="py-2.5 px-3">Jugador / Rival</th>
                                    <th class="py-2.5 px-3">Pos</th>
                                    <th class="py-2.5 px-3 text-right">Valor</th>
                                    <th class="py-2.5 px-3 text-center">Tendencia</th>
                                    <th class="py-2.5 px-3 text-right">Cláusula</th>
                                    <th class="py-2.5 px-3 text-center">Titular</th>
                                    <th class="py-2.5 px-3 text-right">Estado Escudo</th>
                                </tr>
                            </thead>
                            <tbody>
                                {rival_clauses_html}
                            </tbody>
                        </table>
                    </div>
                </div>
            </section>

            <!-- TAB 4: POINTS BREAKDOWN -->
            <section id="tab-points" class="tab-content hidden space-y-4">
                <div class="bg-zinc-900/60 border border-zinc-800/80 rounded-xl p-4">
                    <div class="flex items-center justify-between pb-2.5 border-b border-zinc-800/60">
                        <div>
                            <h3 class="text-xs uppercase font-bold text-zinc-200">Puntuación por Jornada</h3>
                            <p class="text-[11px] text-zinc-500 mt-0.5">Rendimiento acumulado de tu equipo</p>
                        </div>
                        <div class="text-right">
                            <span class="text-[10px] text-zinc-500 block uppercase font-mono">Total</span>
                            <span class="font-bold text-emerald-400 text-sm font-mono">{total_points} Pts</span>
                        </div>
                    </div>
                    <div class="h-48 mt-3">
                        <canvas id="pointsChart"></canvas>
                    </div>
                </div>
            </section>

            <!-- TAB 5: SCHEDULED ACTIONS, REASONING ARCHIVE & AUDIT TIMELINE -->
            <section id="tab-history" class="tab-content hidden space-y-4">
                <!-- Scheduled Actions & Sniping Plan -->
                <div class="bg-zinc-900/60 border border-zinc-800/80 rounded-xl p-3.5 sm:p-4">
                    <div class="flex items-center justify-between pb-2.5 border-b border-zinc-800/60">
                        <div class="flex items-center space-x-2">
                            <span class="text-zinc-400">{ICONS['clock']}</span>
                            <h3 class="text-xs uppercase font-bold text-zinc-200">Acciones Programadas & Sniping</h3>
                        </div>
                        <span class="text-[10px] text-zinc-500 font-mono">{len(scheduled_items)} activa(s)</span>
                    </div>
                    <div class="mt-3">
                        {scheduled_actions_html}
                    </div>
                </div>

                <!-- Gemini Reasoning Archive -->
                <div class="bg-zinc-900/60 border border-zinc-800/80 rounded-xl p-3.5 sm:p-4">
                    <div class="flex items-center justify-between pb-2.5 border-b border-zinc-800/60">
                        <div class="flex items-center space-x-2">
                            <span class="text-zinc-400">{ICONS['cpu']}</span>
                            <h3 class="text-xs uppercase font-bold text-zinc-200">Historial de Razonamientos de Gemini</h3>
                        </div>
                        <span class="text-[10px] text-zinc-500 font-mono">Archivo permanente</span>
                    </div>
                    <div class="mt-3">
                        {reasoning_archive_html}
                    </div>
                </div>

                <!-- Action Audit Timeline -->
                <div class="bg-zinc-900/60 border border-zinc-800/80 rounded-xl p-3.5 sm:p-4">
                    <div class="flex items-center justify-between pb-2.5 border-b border-zinc-800/60">
                        <div class="flex items-center space-x-2">
                            <span class="text-zinc-400">{ICONS['history']}</span>
                            <h3 class="text-xs uppercase font-bold text-zinc-200">Registro Completo de Acciones</h3>
                        </div>
                        <span class="text-[10px] text-zinc-500 font-mono">Auditoría del bot</span>
                    </div>
                    <div class="mt-3">
                        {timeline_html or '<div class="p-4 text-center text-zinc-500 text-xs">Sin acciones registradas</div>'}
                    </div>
                </div>
            </section>
        </div>

        <!-- Footer -->
        <footer class="pt-6 pb-4 border-t border-zinc-800/60 text-center text-[10px] sm:text-[11px] text-zinc-600 font-mono">
            FantasyBot OS • Gemini 3.5 Flash Lite • GitHub Pages
        </footer>
    </main>

    <!-- Scripts for Chart.js and Interactivity -->
    <script>
        // Financial Line Chart
        const ctx = document.getElementById('patrimonyChart').getContext('2d');
        new Chart(ctx, {{
            type: 'line',
            data: {{
                labels: {labels_json},
                datasets: [
                    {{
                        label: 'Patrimonio Total',
                        data: {total_json},
                        borderColor: '#fafafa',
                        backgroundColor: 'rgba(250, 250, 250, 0.02)',
                        borderWidth: 1.8,
                        tension: 0.3,
                        pointRadius: 2,
                        pointHoverRadius: 4,
                    }},
                    {{
                        label: 'Caja',
                        data: {money_json},
                        borderColor: '#10b981',
                        backgroundColor: 'rgba(16, 185, 129, 0.02)',
                        borderWidth: 1.5,
                        tension: 0.3,
                        pointRadius: 2,
                        pointHoverRadius: 4,
                    }},
                    {{
                        label: 'Plantilla',
                        data: {value_json},
                        borderColor: '#71717a',
                        backgroundColor: 'rgba(113, 113, 122, 0.02)',
                        borderWidth: 1.5,
                        tension: 0.3,
                        pointRadius: 2,
                        pointHoverRadius: 4,
                    }}
                ]
            }},
            options: {{
                responsive: true,
                maintainAspectRatio: false,
                layout: {{
                    padding: {{ left: 5, right: 10, top: 5, bottom: 5 }}
                }},
                plugins: {{
                    legend: {{ display: false }},
                    tooltip: {{
                        backgroundColor: '#18181b',
                        borderColor: '#27272a',
                        borderWidth: 1,
                        titleFont: {{ size: 11, family: 'Inter', weight: 'bold' }},
                        bodyFont: {{ size: 11, family: 'JetBrains Mono' }},
                        callbacks: {{
                            label: function(context) {{
                                let val = context.raw || 0;
                                return context.dataset.label + ': ' + (val / 1000000).toFixed(2) + 'M €';
                            }}
                        }}
                    }}
                }},
                scales: {{
                    x: {{
                        grid: {{ color: 'rgba(255, 255, 255, 0.03)' }},
                        ticks: {{ color: '#71717a', font: {{ size: 9, family: 'JetBrains Mono' }} }}
                    }},
                    y: {{
                        grid: {{ color: 'rgba(255, 255, 255, 0.03)' }},
                        ticks: {{
                            color: '#71717a',
                            font: {{ size: 9, family: 'JetBrains Mono' }},
                            padding: 6,
                            callback: function(value) {{
                                let m = value / 1000000;
                                return (m % 1 === 0 ? m.toFixed(0) : m.toFixed(1)) + 'M €';
                            }}
                        }},
                        afterFit: function(axis) {{
                            axis.width = 60;
                        }}
                    }}
                }}
            }}
        }});

        // Points Bar Chart
        const ptsCtx = document.getElementById('pointsChart');
        if (ptsCtx) {{
            new Chart(ptsCtx.getContext('2d'), {{
                type: 'bar',
                data: {{
                    labels: {gw_labels_json},
                    datasets: [{{
                        label: 'Puntos',
                        data: {gw_points_json},
                        backgroundColor: '#10b981',
                        borderRadius: 6,
                        barThickness: 24,
                    }}]
                }},
                options: {{
                    responsive: true,
                    maintainAspectRatio: false,
                    plugins: {{ legend: {{ display: false }} }},
                    scales: {{
                        x: {{
                            grid: {{ display: false }},
                            ticks: {{ color: '#71717a', font: {{ size: 10, family: 'JetBrains Mono' }} }}
                        }},
                        y: {{
                            grid: {{ color: 'rgba(255, 255, 255, 0.03)' }},
                            ticks: {{ color: '#71717a', font: {{ size: 10, family: 'JetBrains Mono' }} }}
                        }}
                    }}
                }}
            }});
        }}

        function switchTab(tabId) {{
            document.querySelectorAll('.tab-content').forEach(el => el.classList.add('hidden'));
            document.querySelectorAll('.tab-btn').forEach(el => {{
                el.classList.remove('tab-active');
                el.classList.add('text-zinc-400');
            }});
            document.getElementById(tabId).classList.remove('hidden');
            const activeBtn = document.getElementById('btn-' + tabId);
            if (activeBtn) {{
                activeBtn.classList.add('tab-active');
                activeBtn.classList.remove('text-zinc-400');
            }}
        }}

        function filterMarket() {{
            const search = document.getElementById('marketSearch').value.toLowerCase();
            const pos = document.getElementById('posFilter').value;
            const rows = document.querySelectorAll('.market-row');

            rows.forEach(row => {{
                const rPos = row.getAttribute('data-pos');
                const rName = row.getAttribute('data-name');
                const matchesPos = (pos === 'ALL' || rPos === pos);
                const matchesSearch = rName.includes(search);

                if (matchesPos && matchesSearch) {{
                    row.style.display = '';
                }} else {{
                    row.style.display = 'none';
                }}
            }});
        }}
    </script>
</body>
</html>
    """

    # Write output to public/index.html
    public_dir = os.path.join(config.ROOT, "public")
    os.makedirs(public_dir, exist_ok=True)
    out_file = os.path.join(public_dir, "index.html")
    with open(out_file, "w", encoding="utf-8") as f:
        f.write(html_content)
    print(f"\n[OK] Panel Apple Dark Mode Minimalista generado exitosamente en public/index.html")
