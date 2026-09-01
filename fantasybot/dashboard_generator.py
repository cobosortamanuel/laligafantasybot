"""Apple-Style Minimalist Dark Mode Dashboard Generator for FantasyBot.

Features:
- Full complete AI Report renderer with clean formatting and code blocks (never cut off).
- Clickable and expandable interactive Activity History with details and raw JSON payload.
- Generous breathing room and vertical padding between sections and tabs.
- Clean SVG vector icons (0 emojis).
- Refined Apple Monochrome / Dark palette (Zinc/Neutral/Emerald minimal accents).
- Full interactive Chart.js, squad grid, market table with live search and filters.
"""

import html as html_lib
import json
import os
import re
from datetime import datetime, timezone

from . import config, events, state
from .sources.market_trends import market_trends


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

    # If an entry for today already exists, update today's values; otherwise append a new day
    if history and (history[-1].get("label") == today_label or history[-1].get("date") == today_date):
        history[-1] = entry
    else:
        history.append(entry)
        
    history = history[-90:]  # Keep last 90 daily points
    try:
        with open(history_file, "w", encoding="utf-8") as f:
            json.dump(history, f, indent=2)
    except Exception:
        pass
    return history


def _format_kickoff(review_report):
    """Formats matchday info cleanly without raw ISOs or negative countdowns."""
    md = review_report.get("matchday", {}) if review_report else {}
    iso = md.get("kickoff")

    if not iso:
        return "Por confirmar", "Próximamente"

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
        return "Por confirmar", "Próximamente"


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


# Clean Vector SVG Icons
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
    "trending_up": '<svg class="w-4 h-4 text-emerald-400" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M13 7h8m0 0v8m0-8l-8 8-4-4-6 6"/></svg>',
    "trending_down": '<svg class="w-4 h-4 text-zinc-400" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M13 17h8m0 0V9m0 8l-8-8-4 4-6-6"/></svg>',
    "target": '<svg class="w-4 h-4 text-zinc-300" fill="none" stroke="currentColor" viewBox="0 0 24 24"><circle cx="12" cy="12" r="10" stroke-width="1.8"/><circle cx="12" cy="12" r="6" stroke-width="1.8"/><circle cx="12" cy="12" r="2" stroke-width="1.8"/></svg>',
    "search": '<svg class="w-3.5 h-3.5 text-zinc-500" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M21 21l-6-6m2-5a7 7 0 11-14 0 7 7 0 0114 0z"/></svg>'
}


def generate_apple_dashboard(team, market, best_lineup, flips, gaps, review_report, gemini_response, decision, executed, prob_index=None):
    """Builds the refined minimalist Apple dark mode HTML dashboard."""
    now = datetime.now()
    now_str = now.strftime("%d/%m/%Y a las %H:%M")
    
    money = team.get("teamMoney", 0)
    value = team.get("teamValue", 0)
    total_patrimony = money + value
    manager_name = team.get("managerName") or "Real Betis Frigopie"
    
    # Save & fetch chart history
    history = update_history_state(money, value)
    if len(history) < 2:
        history = [
            {"label": "Inicio", "money": money, "value": value, "total": total_patrimony},
            {"label": now.strftime("%d/%m %H:%M"), "money": money, "value": value, "total": total_patrimony}
        ]

    # Map positions
    pos_map = {1: "POR", 2: "DEF", 3: "MED", 4: "DEL", 5: "ENT"}
    pos_badge = {
        "POR": "bg-zinc-800/90 text-zinc-300 border-zinc-700/60",
        "DEF": "bg-zinc-800/90 text-zinc-300 border-zinc-700/60",
        "MED": "bg-zinc-800/90 text-zinc-300 border-zinc-700/60",
        "DEL": "bg-zinc-800/90 text-zinc-300 border-zinc-700/60",
        "ENT": "bg-zinc-800/90 text-zinc-300 border-zinc-700/60",
        "JUG": "bg-zinc-800/90 text-zinc-300 border-zinc-700/60"
    }

    # Extract Squad
    players = team.get("players", [])
    squad_cards_html = ""
    for p in players:
        pm = p.get("playerMaster", {})
        pt = p.get("playerTeam", {})
        p_name = pm.get("nickname") or pm.get("name") or "Desconocido"
        pos_id = pm.get("positionId")
        pos_str = pos_map.get(pos_id, "JUG")
        p_val = pt.get("marketValue") or pm.get("marketValue") or 0
        img = pm.get("images", {}).get("transparent", {}).get("256x256") or "https://assets-fantasy.llt-services.com/players/default.png"
        points = pm.get("points", 0)
        avg = pm.get("averagePoints", 0)
        status = pm.get("playerStatus", "ok")
        clause = pt.get("buyoutClause") or (p_val * 1.67)

        prob = None
        if prob_index:
            from .matching import match_name
            minfo = match_name(pm.get("nickname", ""), pm.get("name", ""), prob_index)
            if minfo:
                prob = minfo.get("prob")

        status_badge = '<span class="inline-flex items-center text-[10px] font-medium text-emerald-400 bg-emerald-500/10 px-2 py-0.5 rounded border border-emerald-500/20">Disponible</span>'
        if status in ("lesionado", "injured"):
            status_badge = '<span class="inline-flex items-center text-[10px] font-medium text-zinc-400 bg-zinc-800 px-2 py-0.5 rounded border border-zinc-700">Baja / Lesión</span>'
        elif status in ("sancionado", "suspended"):
            status_badge = '<span class="inline-flex items-center text-[10px] font-medium text-zinc-400 bg-zinc-800 px-2 py-0.5 rounded border border-zinc-700">Sancionado</span>'

        squad_cards_html += f"""
        <div class="bg-zinc-900/60 border border-zinc-800/80 rounded-xl p-4 hover:border-zinc-700 transition-all">
            <div class="flex items-start justify-between">
                <span class="px-2 py-0.5 text-[11px] font-mono font-semibold rounded border {pos_badge.get(pos_str, pos_badge['JUG'])}">{pos_str}</span>
                {status_badge}
            </div>
            <div class="flex items-center space-x-3 my-3">
                <img src="{img}" alt="{p_name}" class="w-12 h-12 rounded-lg object-cover bg-zinc-800/80 p-0.5 border border-zinc-800" onerror="this.src='https://assets-fantasy.llt-services.com/players/default.png'">
                <div class="min-w-0 flex-1">
                    <h4 class="font-semibold text-zinc-100 text-sm truncate">{p_name}</h4>
                    <p class="text-[11px] text-zinc-400 mt-0.5">Cláusula: {_format_money(int(clause))}</p>
                    <p class="text-xs font-semibold text-zinc-200 mt-0.5">{_format_money(p_val)}</p>
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
                    <span class="font-semibold text-emerald-400 text-xs">{f"{prob}%" if prob is not None else "100%"}</span>
                </div>
            </div>
        </div>
        """

    # Extract Full Market
    tid_str = str(team.get("id"))
    market_rows_html = ""
    for m in market:
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

        is_mine = str(m.get("team", {}).get("id")) == tid_str or str(pt.get("teamId")) == tid_str
        owner_tag = '<span class="text-[10px] font-medium text-emerald-400 bg-emerald-500/10 px-1.5 py-0.5 rounded border border-emerald-500/20">Tu Equipo</span>' if is_mine else ('<span class="text-[10px] text-zinc-400 bg-zinc-800/80 px-1.5 py-0.5 rounded border border-zinc-700/60">Sistema</span>' if m.get("discr") == "marketPlayerLeague" else '<span class="text-[10px] text-zinc-300 bg-zinc-800 px-1.5 py-0.5 rounded border border-zinc-700">Rival</span>')

        bids_badge = f'<span class="text-xs text-zinc-400">{bids_count}</span>'
        if bids_count > 0:
            bids_badge = f'<span class="text-xs font-semibold text-zinc-100 bg-zinc-800 px-2 py-0.5 rounded border border-zinc-700">{bids_count} puja(s)</span>'

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
            <td class="py-2.5 px-3">{owner_tag}</td>
            <td class="py-2.5 px-3 text-right font-semibold text-zinc-200 text-xs">{_format_money(sale_price)}</td>
            <td class="py-2.5 px-3 text-center">{bids_badge}</td>
            <td class="py-2.5 px-3 text-right text-[11px] text-zinc-400 font-mono">{time_left_str}</td>
        </tr>
        """

    # Top Gainers / Losers from Market Trends
    top_up_html = ""
    top_down_html = ""
    try:
        all_trends = [p for p in market_trends() if p.get("tendencia") is not None]
        up_list = sorted(all_trends, key=lambda x: -x["tendencia"])[:6]
        down_list = sorted(all_trends, key=lambda x: x["tendencia"])[:6]

        for u in up_list:
            top_up_html += f"""
            <div class="flex items-center justify-between p-2.5 bg-zinc-900/60 border border-zinc-800/80 rounded-lg">
                <div>
                    <span class="font-semibold text-zinc-200 text-xs block">{u.get('nombre', '').title()}</span>
                    <span class="text-[11px] text-zinc-500 font-mono">{_format_money(u.get('valor', 0))}</span>
                </div>
                <div class="text-right">
                    <span class="px-2 py-0.5 rounded text-[11px] font-mono font-semibold bg-emerald-500/10 text-emerald-400 border border-emerald-500/20">+{u.get('tendencia')}%</span>
                    <span class="text-[10px] text-emerald-400/80 block mt-0.5 font-mono">+{_format_money(u.get('aceleracion', 0))}</span>
                </div>
            </div>
            """

        for d in down_list:
            top_down_html += f"""
            <div class="flex items-center justify-between p-2.5 bg-zinc-900/60 border border-zinc-800/80 rounded-lg">
                <div>
                    <span class="font-semibold text-zinc-200 text-xs block">{d.get('nombre', '').title()}</span>
                    <span class="text-[11px] text-zinc-500 font-mono">{_format_money(d.get('valor', 0))}</span>
                </div>
                <div class="text-right">
                    <span class="px-2 py-0.5 rounded text-[11px] font-mono font-semibold bg-zinc-800 text-zinc-400 border border-zinc-700">{d.get('tendencia')}%</span>
                    <span class="text-[10px] text-zinc-400 block mt-0.5 font-mono">{_format_money(d.get('aceleracion', 0))}</span>
                </div>
            </div>
            """
    except Exception:
        top_up_html = "<div class='text-zinc-500 text-xs p-3'>Datos no disponibles</div>"
        top_down_html = "<div class='text-zinc-500 text-xs p-3'>Datos no disponibles</div>"

    # Scheduled Plan / Bid Targets
    bid_plan = state.load_bid_plan()
    plan_cards_html = ""
    if bid_plan:
        for t in bid_plan:
            m_id = t.get("market_id")
            m_cap = t.get("max_bid", 0)
            p_name = t.get("nombre") or f"Jugador #{m_id}"
            plan_cards_html += f"""
            <div class="bg-zinc-900/70 border border-zinc-800 rounded-lg p-3 flex items-center justify-between">
                <div class="flex items-center space-x-2.5">
                    <div class="w-8 h-8 rounded bg-zinc-800 border border-zinc-700 flex items-center justify-center text-zinc-300">
                        {ICONS['target']}
                    </div>
                    <div>
                        <h4 class="font-semibold text-zinc-100 text-xs">{p_name}</h4>
                        <p class="text-[10px] text-zinc-400 font-mono">ID: {m_id} • Disparo 25s antes de cierre</p>
                    </div>
                </div>
                <div class="text-right">
                    <span class="text-[10px] text-zinc-500 block uppercase font-mono">Tope</span>
                    <span class="font-semibold text-emerald-400 text-xs">{_format_money(m_cap)}</span>
                </div>
            </div>
            """
    else:
        plan_cards_html = """
        <div class="p-4 text-center text-zinc-500 text-xs bg-zinc-900/40 border border-zinc-800/60 rounded-lg">
            No hay pujas pendientes en cola. El agente programará nuevos objetivos en el próximo pase.
        </div>
        """

    # Interactive & Expandable Activity Timeline (Clickable Details)
    recent_events = events.load(limit=25)
    timeline_html = ""
    for ev in reversed(recent_events):
        k = ev.get("kind", "note")
        title = ev.get("title", "")
        ts = ev.get("iso", "")[:16].replace("T", " ")
        detail_obj = ev.get("detail")
        detail_json = ""
        if detail_obj:
            try:
                detail_json = json.dumps(detail_obj, ensure_ascii=False, indent=2)
            except Exception:
                detail_json = str(detail_obj)

        timeline_html += f"""
        <details class="group bg-zinc-900/50 hover:bg-zinc-900/80 border border-zinc-800/80 rounded-xl transition-all overflow-hidden">
            <summary class="flex items-center justify-between p-3 cursor-pointer list-none select-none">
                <div class="flex items-center space-x-3 min-w-0">
                    <span class="p-1.5 bg-zinc-800 rounded-lg border border-zinc-700 text-zinc-300 flex-shrink-0">{ICONS['clock']}</span>
                    <div class="min-w-0">
                        <div class="font-medium text-zinc-200 text-xs truncate">{title}</div>
                        <div class="text-[10px] text-zinc-500 font-mono mt-0.5 uppercase tracking-wider">{k} • {ts}</div>
                    </div>
                </div>
                <div class="flex items-center space-x-2 text-zinc-500 text-xs font-mono group-open:rotate-180 transition-transform">
                    <span>▼</span>
                </div>
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

    # Full Markdown Formatted Gemini Report
    formatted_gemini = _format_markdown_report(gemini_response)

    # Matchday formatting
    kickoff_text, countdown_text = _format_kickoff(review_report)

    # Chart.js JSON data
    labels_json = json.dumps([h["label"] for h in history])
    money_json = json.dumps([h["money"] for h in history])
    value_json = json.dumps([h["value"] for h in history])
    total_json = json.dumps([h["total"] for h in history])

    # Minimalist Apple Dark Mode Template with Generous Spacing
    html_content = f"""<!DOCTYPE html>
<html lang="es" class="dark">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>FantasyBot OS • Panel de Control</title>
    <script src="https://cdn.tailwindcss.com"></script>
    <script src="https://cdn.jsdelivr.net/npm/chart.js"></script>
    <link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&family=JetBrains+Mono:wght@400;500;600&display=swap" rel="stylesheet">
    <style>
        body {{
            background-color: #09090b;
            color: #f4f4f5;
            font-family: 'Inter', -apple-system, BlinkMacSystemFont, sans-serif;
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
            width: 5px;
            height: 5px;
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
    </style>
</head>
<body class="min-h-screen antialiased selection:bg-zinc-800 selection:text-white">
    <!-- Apple Minimalist Top Header -->
    <header class="border-b border-zinc-800/80 bg-zinc-950/80 backdrop-blur sticky top-0 z-50 px-4 py-3">
        <div class="max-w-6xl mx-auto flex items-center justify-between">
            <div class="flex items-center space-x-2.5">
                <div class="w-6 h-6 rounded bg-zinc-800 border border-zinc-700 flex items-center justify-center text-zinc-300">
                    {ICONS['shield']}
                </div>
                <div class="flex items-center space-x-2">
                    <span class="font-semibold text-zinc-100 text-sm tracking-tight">{manager_name}</span>
                    <span class="text-zinc-600 text-xs">•</span>
                    <span class="text-xs text-zinc-400 font-mono">FantasyBot OS</span>
                </div>
            </div>

            <div class="flex items-center space-x-3">
                <div class="flex items-center space-x-1.5 text-xs text-zinc-400 font-mono">
                    <span class="w-1.5 h-1.5 rounded-full bg-emerald-400 animate-pulse"></span>
                    <span>Activo (09:00 / 20:15)</span>
                </div>
                <span class="text-[11px] font-mono px-2 py-0.5 rounded border {('bg-emerald-500/10 text-emerald-400 border-emerald-500/20' if executed else 'bg-zinc-800 text-zinc-400 border-zinc-700')}">
                    {'Ejecutado' if executed else 'Análisis'}
                </span>
            </div>
        </div>
    </header>

    <!-- Main Container with Balanced Spacing -->
    <main class="max-w-6xl mx-auto px-4 py-5 space-y-4">
        
        <!-- Metrics Bento Grid -->
        <section class="grid grid-cols-2 md:grid-cols-4 gap-3">
            <!-- Box 1: Presupuesto -->
            <div class="bg-zinc-900/60 border border-zinc-800/80 rounded-xl p-3.5">
                <div class="flex items-center justify-between text-zinc-400 text-[11px] uppercase tracking-wider font-semibold">
                    <span>Presupuesto</span>
                    <span class="text-zinc-400">{ICONS['wallet']}</span>
                </div>
                <div class="mt-1.5">
                    <div class="text-xl font-bold text-zinc-100 font-mono tracking-tight">{_format_money(money)}</div>
                    <p class="text-[11px] text-zinc-500 mt-0.5">Disponible en caja</p>
                </div>
            </div>

            <!-- Box 2: Valor Plantilla -->
            <div class="bg-zinc-900/60 border border-zinc-800/80 rounded-xl p-3.5">
                <div class="flex items-center justify-between text-zinc-400 text-[11px] uppercase tracking-wider font-semibold">
                    <span>Plantilla</span>
                    <span class="text-zinc-400">{ICONS['users']}</span>
                </div>
                <div class="mt-1.5">
                    <div class="text-xl font-bold text-zinc-100 font-mono tracking-tight">{_format_money(value)}</div>
                    <p class="text-[11px] text-zinc-500 mt-0.5">{len(players)} jugadores en nómina</p>
                </div>
            </div>

            <!-- Box 3: Total Club -->
            <div class="bg-zinc-900/60 border border-zinc-800/80 rounded-xl p-3.5">
                <div class="flex items-center justify-between text-zinc-400 text-[11px] uppercase tracking-wider font-semibold">
                    <span>Patrimonio Total</span>
                    <span class="text-zinc-400">{ICONS['chart']}</span>
                </div>
                <div class="mt-1.5">
                    <div class="text-xl font-bold text-zinc-100 font-mono tracking-tight">{_format_money(total_patrimony)}</div>
                    <p class="text-[11px] text-zinc-500 mt-0.5">Activos netos</p>
                </div>
            </div>

            <!-- Box 4: Próxima Jornada -->
            <div class="bg-zinc-900/60 border border-zinc-800/80 rounded-xl p-3.5">
                <div class="flex items-center justify-between text-zinc-400 text-[11px] uppercase tracking-wider font-semibold">
                    <span>Próxima Jornada</span>
                    <span class="text-zinc-400">{ICONS['calendar']}</span>
                </div>
                <div class="mt-1.5">
                    <div class="text-xs font-semibold text-zinc-200 truncate">{kickoff_text}</div>
                    <p class="text-[11px] text-emerald-400 font-mono mt-0.5 font-medium">{countdown_text}</p>
                </div>
            </div>
        </section>

        <!-- Chart Section: Evolución Financiera -->
        <section class="bg-zinc-900/60 border border-zinc-800/80 rounded-xl p-4 md:p-4.5">
            <div class="flex items-center justify-between pb-3 border-b border-zinc-800/60">
                <div>
                    <h2 class="text-xs uppercase tracking-wider font-bold text-zinc-300">Evolución Financiera</h2>
                    <p class="text-[11px] text-zinc-500 mt-0.5">Historial acumulado de presupuesto y plantilla</p>
                </div>
                <div class="flex items-center space-x-3 text-[11px] font-mono text-zinc-400">
                    <span class="flex items-center space-x-1"><span class="w-2 h-2 rounded-full bg-zinc-100"></span><span>Patrimonio</span></span>
                    <span class="flex items-center space-x-1"><span class="w-2 h-2 rounded-full bg-emerald-400"></span><span>Caja</span></span>
                    <span class="flex items-center space-x-1"><span class="w-2 h-2 rounded-full bg-zinc-500"></span><span>Plantilla</span></span>
                </div>
            </div>
            <div class="h-52 mt-3">
                <canvas id="patrimonyChart"></canvas>
            </div>
        </section>

        <!-- AI Tactical Brain Analysis (Full Report, Never Truncated) -->
        <section class="bg-zinc-900/60 border border-zinc-800/80 rounded-xl p-4 md:p-4.5">
            <div class="flex items-center justify-between pb-3 border-b border-zinc-800/60">
                <div class="flex items-center space-x-2">
                    <span class="text-zinc-300">{ICONS['cpu']}</span>
                    <h2 class="text-xs uppercase tracking-wider font-bold text-zinc-200">Informe Táctico de Gemini</h2>
                </div>
                <span class="text-[10px] text-zinc-500 font-mono">{now_str}</span>
            </div>
            <div class="mt-3 text-xs text-zinc-300 leading-relaxed space-y-1.5">
                {formatted_gemini}
            </div>
        </section>

        <!-- Navigation Tabs with Balanced Spacing -->
        <nav class="pt-2 pb-1.5 border-b border-zinc-800/80">
            <div class="flex items-center space-x-1.5 overflow-x-auto pb-1 text-xs font-medium">
                <button onclick="switchTab('tab-squad')" id="btn-tab-squad" class="tab-btn tab-active px-3 py-1.5 rounded-lg border border-transparent transition-all flex items-center space-x-1.5">
                    <span>{ICONS['users']}</span>
                    <span>Plantilla ({len(players)})</span>
                </button>
                <button onclick="switchTab('tab-market')" id="btn-tab-market" class="tab-btn px-3 py-1.5 rounded-lg text-zinc-400 hover:text-zinc-200 border border-transparent transition-all flex items-center space-x-1.5">
                    <span>{ICONS['market']}</span>
                    <span>Mercado ({len(market)})</span>
                </button>
                <button onclick="switchTab('tab-trends')" id="btn-tab-trends" class="tab-btn px-3 py-1.5 rounded-lg text-zinc-400 hover:text-zinc-200 border border-transparent transition-all flex items-center space-x-1.5">
                    <span>{ICONS['chart']}</span>
                    <span>Tendencias</span>
                </button>
                <button onclick="switchTab('tab-plan')" id="btn-tab-plan" class="tab-btn px-3 py-1.5 rounded-lg text-zinc-400 hover:text-zinc-200 border border-transparent transition-all flex items-center space-x-1.5">
                    <span>{ICONS['clock']}</span>
                    <span>Plan de Pujas</span>
                </button>
                <button onclick="switchTab('tab-history')" id="btn-tab-history" class="tab-btn px-3 py-1.5 rounded-lg text-zinc-400 hover:text-zinc-200 border border-transparent transition-all flex items-center space-x-1.5">
                    <span>{ICONS['history']}</span>
                    <span>Historial</span>
                </button>
            </div>
        </nav>

        <!-- Tab Content Container -->
        <div class="pt-2">
            <!-- TAB 1: SQUAD -->
            <section id="tab-squad" class="tab-content space-y-4">
                <div class="flex items-center justify-between pb-1">
                    <span class="text-xs text-zinc-400">Todos los futbolistas están a la venta para recibir ofertas diarias.</span>
                    <span class="text-[11px] text-zinc-500 font-mono">Huecos: {', '.join(gaps) if gaps else 'Ninguno'}</span>
                </div>
                <div class="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-3.5">
                    {squad_cards_html or '<div class="col-span-4 p-6 text-center text-zinc-500 text-xs">Plantilla vacía</div>'}
                </div>
            </section>

            <!-- TAB 2: MARKET -->
            <section id="tab-market" class="tab-content hidden space-y-4">
                <div class="flex flex-col sm:flex-row sm:items-center justify-between gap-3 pb-1">
                    <span class="text-xs text-zinc-400">Listado en tiempo real de jugadores disponibles.</span>
                    <div class="flex items-center space-x-2">
                        <div class="relative">
                            <span class="absolute inset-y-0 left-0 flex items-center pl-2.5 pointer-events-none">{ICONS['search']}</span>
                            <input type="text" id="marketSearch" onkeyup="filterMarket()" placeholder="Buscar jugador..." class="bg-zinc-900 border border-zinc-800 text-zinc-200 text-xs pl-7 pr-3 py-1.5 rounded-lg focus:outline-none focus:border-zinc-700 w-48">
                        </div>
                        <select id="posFilter" onchange="filterMarket()" class="bg-zinc-900 border border-zinc-800 text-zinc-300 text-xs px-2.5 py-1.5 rounded-lg focus:outline-none focus:border-zinc-700">
                            <option value="ALL">Todas las pos.</option>
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
                                    <th class="py-3 px-3.5">Jugador</th>
                                    <th class="py-3 px-3.5">Pos</th>
                                    <th class="py-3 px-3.5">Origen</th>
                                    <th class="py-3 px-3.5 text-right">Precio Salida</th>
                                    <th class="py-3 px-3.5 text-center">Pujas</th>
                                    <th class="py-3 px-3.5 text-right">Cierre</th>
                                </tr>
                            </thead>
                            <tbody>
                                {market_rows_html}
                            </tbody>
                        </table>
                    </div>
                </div>
            </section>

            <!-- TAB 3: TRENDS -->
            <section id="tab-trends" class="tab-content hidden space-y-4">
                <div class="grid grid-cols-1 md:grid-cols-2 gap-4">
                    <!-- Top Gainers -->
                    <div class="bg-zinc-900/60 border border-zinc-800/80 rounded-xl p-4">
                        <div class="flex items-center justify-between mb-3 pb-2 border-b border-zinc-800/60">
                            <div class="flex items-center space-x-1.5">
                                {ICONS['trending_up']}
                                <h3 class="text-xs uppercase font-bold text-zinc-200">Mayores Subidas</h3>
                            </div>
                            <span class="text-[10px] text-zinc-500 font-mono">En alza</span>
                        </div>
                        <div class="space-y-2">
                            {top_up_html}
                        </div>
                    </div>

                    <!-- Top Losers -->
                    <div class="bg-zinc-900/60 border border-zinc-800/80 rounded-xl p-4">
                        <div class="flex items-center justify-between mb-3 pb-2 border-b border-zinc-800/60">
                            <div class="flex items-center space-x-1.5">
                                {ICONS['trending_down']}
                                <h3 class="text-xs uppercase font-bold text-zinc-200">Mayores Caídas</h3>
                            </div>
                            <span class="text-[10px] text-zinc-500 font-mono">Depreciación</span>
                        </div>
                        <div class="space-y-2">
                            {top_down_html}
                        </div>
                    </div>
                </div>
            </section>

            <!-- TAB 4: SCHEDULED PLAN -->
            <section id="tab-plan" class="tab-content hidden space-y-4">
                <div>
                    <h3 class="text-xs uppercase font-bold text-zinc-200">Plan de Sniping de Último Minuto</h3>
                    <p class="text-[11px] text-zinc-500 mt-0.5">Pujas programadas para ejecutarse a 25 segundos del cierre.</p>
                </div>
                <div class="space-y-2.5">
                    {plan_cards_html}
                </div>

                <div class="bg-zinc-900/60 border border-zinc-800/80 rounded-xl p-4 mt-6">
                    <h4 class="text-xs uppercase font-bold text-zinc-300 mb-2.5">Horarios de Automatización</h4>
                    <div class="grid grid-cols-1 md:grid-cols-2 gap-3 text-xs text-zinc-400">
                        <div class="bg-zinc-950/60 p-3.5 rounded-lg border border-zinc-800/60">
                            <span class="font-semibold text-zinc-200 block text-xs mb-0.5 font-mono">09:00 (Hora España) • Matinal</span>
                            Escaneo de nuevos jugadores, detección de ofertas entrantes y planificación del día.
                        </div>
                        <div class="bg-zinc-950/60 p-3.5 rounded-lg border border-zinc-800/60">
                            <span class="font-semibold text-zinc-200 block text-xs mb-0.5 font-mono">20:15 (Hora España) • Cierre</span>
                            Disparo de pujas de último minuto (+210€ o competitivas) y confirmación de alineación.
                        </div>
                    </div>
                </div>
            </section>

            <!-- TAB 5: TIMELINE / AUDIT (Clickable & Expandable) -->
            <section id="tab-history" class="tab-content hidden space-y-4">
                <div>
                    <h3 class="text-xs uppercase font-bold text-zinc-200">Registro de Actividad</h3>
                    <p class="text-[11px] text-zinc-500 mt-0.5">Haz clic en cualquier evento para desplegar sus detalles completos y datos técnicos.</p>
                </div>
                <div class="space-y-2">
                    {timeline_html or '<div class="p-6 text-center text-zinc-500 text-xs">Sin eventos registrados</div>'}
                </div>
            </section>
        </div>

        <!-- Footer -->
        <footer class="pt-8 pb-4 border-t border-zinc-800/60 text-center text-[11px] text-zinc-600 font-mono">
            FantasyBot OS • Gemini 3.5 Flash Lite • GitHub Pages
        </footer>
    </main>

    <!-- Scripts for Chart.js and Interactivity -->
    <script>
        const ctx = document.getElementById('patrimonyChart').getContext('2d');
        const labels = {labels_json};
        const moneyData = {money_json};
        const valueData = {value_json};
        const totalData = {total_json};

        new Chart(ctx, {{
            type: 'line',
            data: {{
                labels: labels,
                datasets: [
                    {{
                        label: 'Total Patrimonio',
                        data: totalData,
                        borderColor: '#fafafa',
                        backgroundColor: 'rgba(250, 250, 250, 0.03)',
                        borderWidth: 1.8,
                        tension: 0.3,
                        pointRadius: 2.5,
                        pointHoverRadius: 5,
                    }},
                    {{
                        label: 'Caja / Presupuesto',
                        data: moneyData,
                        borderColor: '#10b981',
                        backgroundColor: 'rgba(16, 185, 129, 0.03)',
                        borderWidth: 1.5,
                        tension: 0.3,
                        pointRadius: 2.5,
                        pointHoverRadius: 5,
                    }},
                    {{
                        label: 'Valor Plantilla',
                        data: valueData,
                        borderColor: '#71717a',
                        backgroundColor: 'rgba(113, 113, 122, 0.03)',
                        borderWidth: 1.5,
                        tension: 0.3,
                        pointRadius: 2.5,
                        pointHoverRadius: 5,
                    }}
                ]
            }},
            options: {{
                responsive: true,
                maintainAspectRatio: false,
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
                            callback: function(value) {{
                                return (value / 1000000).toFixed(1) + 'M €';
                            }}
                        }}
                    }}
                }}
            }}
        }});

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
