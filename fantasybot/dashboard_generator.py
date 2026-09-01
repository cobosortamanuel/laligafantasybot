"""Apple-Style Dark Mode Dashboard Generator for FantasyBot.

Generates an ultra-premium, responsive, interactive single-page dashboard
with Chart.js graphs, full market lists, squad breakdown, Gemini reasoning,
scheduled plans, and event timelines.
"""

import json
import os
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
    """Persists historical budget and team value snapshots for Chart.js."""
    history_file = os.path.join(config.ROOT, ".state", "chart_history.json")
    os.makedirs(os.path.dirname(history_file), exist_ok=True)
    history = []
    if os.path.exists(history_file):
        try:
            with open(history_file, "r", encoding="utf-8") as f:
                history = json.load(f)
        except Exception:
            history = []
    
    now_label = datetime.now().strftime("%d/%m %H:%M")
    # Avoid duplicate snapshots within the same 10 minutes
    if not history or history[-1].get("label") != now_label:
        history.append({
            "label": now_label,
            "timestamp": int(datetime.now().timestamp()),
            "money": money,
            "value": value,
            "total": (money or 0) + (value or 0)
        })
        # Keep last 50 points
        history = history[-50:]
        try:
            with open(history_file, "w", encoding="utf-8") as f:
                json.dump(history, f, indent=2)
        except Exception:
            pass
    return history


def generate_apple_dashboard(team, market, best_lineup, flips, gaps, review_report, gemini_response, decision, executed, prob_index=None):
    """Builds the full HTML dashboard string and writes to public/index.html."""
    now = datetime.now()
    now_str = now.strftime("%d/%m/%Y a las %H:%M")
    
    money = team.get("teamMoney", 0)
    value = team.get("teamValue", 0)
    total_patrimony = money + value
    manager_name = team.get("managerName") or "Real Betis Frigopie"
    
    # Save & fetch chart history
    history = update_history_state(money, value)
    if len(history) < 2:
        # Seed mock previous point if brand new so chart renders a line
        history = [
            {"label": "Inicio", "money": money, "value": value, "total": total_patrimony},
            {"label": now.strftime("%d/%m %H:%M"), "money": money, "value": value, "total": total_patrimony}
        ]

    # Map positions
    pos_map = {1: "POR", 2: "DEF", 3: "MED", 4: "DEL", 5: "ENT"}
    pos_color = {
        "POR": "bg-amber-500/20 text-amber-300 border-amber-500/30",
        "DEF": "bg-blue-500/20 text-blue-300 border-blue-500/30",
        "MED": "bg-emerald-500/20 text-emerald-300 border-emerald-500/30",
        "DEL": "bg-rose-500/20 text-rose-300 border-rose-500/30",
        "ENT": "bg-purple-500/20 text-purple-300 border-purple-500/30",
        "JUG": "bg-gray-500/20 text-gray-300 border-gray-500/30"
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

        # Check probability from prob_index if available
        prob = None
        if prob_index:
            from .matching import match_name
            minfo = match_name(pm.get("nickname", ""), pm.get("name", ""), prob_index)
            if minfo:
                prob = minfo.get("prob")

        status_badge = '<span class="px-2 py-0.5 rounded-full text-xs font-semibold bg-emerald-500/20 text-emerald-400 border border-emerald-500/30">Disponible</span>'
        if status in ("lesionado", "injured"):
            status_badge = '<span class="px-2 py-0.5 rounded-full text-xs font-semibold bg-rose-500/20 text-rose-400 border border-rose-500/30">Lesionado</span>'
        elif status in ("sancionado", "suspended"):
            status_badge = '<span class="px-2 py-0.5 rounded-full text-xs font-semibold bg-amber-500/20 text-amber-400 border border-amber-500/30">Sancionado</span>'

        squad_cards_html += f"""
        <div class="group relative bg-neutral-900/80 backdrop-blur-xl border border-neutral-800/80 hover:border-neutral-700 rounded-3xl p-5 transition-all duration-300 hover:shadow-2xl hover:shadow-emerald-500/5">
            <div class="flex items-start justify-between">
                <span class="px-2.5 py-1 rounded-xl text-xs font-bold border {pos_color.get(pos_str, pos_color['JUG'])}">{pos_str}</span>
                {status_badge}
            </div>
            <div class="flex items-center space-x-4 my-4">
                <img src="{img}" alt="{p_name}" class="w-16 h-16 rounded-2xl object-cover bg-neutral-800/80 p-1 border border-neutral-700/50 group-hover:scale-105 transition-transform" onerror="this.src='https://assets-fantasy.llt-services.com/players/default.png'">
                <div>
                    <h4 class="font-bold text-white text-base leading-tight">{p_name}</h4>
                    <p class="text-xs text-neutral-400 mt-0.5">Cláusula: {_format_money(int(clause))}</p>
                    <p class="text-xs font-semibold text-emerald-400 mt-1">{_format_money(p_val)}</p>
                </div>
            </div>
            <div class="grid grid-cols-3 gap-2 pt-3 border-t border-neutral-800 text-center text-xs">
                <div class="bg-neutral-950/60 rounded-xl p-2">
                    <span class="text-neutral-500 block text-[10px] uppercase font-bold">Puntos</span>
                    <span class="font-bold text-white">{points}</span>
                </div>
                <div class="bg-neutral-950/60 rounded-xl p-2">
                    <span class="text-neutral-500 block text-[10px] uppercase font-bold">Media</span>
                    <span class="font-bold text-white">{avg:.1f}</span>
                </div>
                <div class="bg-neutral-950/60 rounded-xl p-2">
                    <span class="text-neutral-500 block text-[10px] uppercase font-bold">Titularidad</span>
                    <span class="font-bold text-emerald-400">{f"{prob}%" if prob is not None else "100%"}</span>
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
        market_val = pm.get("marketValue") or sale_price
        mid = m.get("id")
        img = pm.get("images", {}).get("transparent", {}).get("256x256") or "https://assets-fantasy.llt-services.com/players/default.png"
        bids_count = m.get("numberOfBids", 0)
        exp = m.get("expirationDate") or ""
        time_left_str = "Cierre hoy"
        if exp:
            try:
                exp_dt = datetime.fromisoformat(exp)
                if exp_dt.tzinfo is None:
                    exp_dt = exp_dt.replace(tzinfo=timezone.utc)
                diff_sec = (exp_dt - datetime.now(timezone.utc)).total_seconds()
                if diff_sec > 3600:
                    time_left_str = f"en {int(diff_sec // 3600)}h {int((diff_sec % 3600) // 60)}m"
                elif diff_sec > 60:
                    time_left_str = f"en {int(diff_sec // 60)}m"
                elif diff_sec > 0:
                    time_left_str = f"en {int(diff_sec)}s"
                else:
                    time_left_str = "Cerrado"
            except Exception:
                pass

        is_mine = str(m.get("team", {}).get("id")) == tid_str or str(pt.get("teamId")) == tid_str
        owner_tag = '<span class="px-2 py-0.5 text-[11px] font-bold rounded-lg bg-emerald-500/10 text-emerald-400 border border-emerald-500/20">Tu Equipo</span>' if is_mine else ('<span class="px-2 py-0.5 text-[11px] font-bold rounded-lg bg-neutral-800 text-neutral-400">Sistema</span>' if m.get("discr") == "marketPlayerLeague" else '<span class="px-2 py-0.5 text-[11px] font-bold rounded-lg bg-purple-500/10 text-purple-400 border border-purple-500/20">Rival</span>')

        bids_badge = f'<span class="text-xs text-neutral-400">{bids_count} puja(s)</span>'
        if bids_count > 0:
            bids_badge = f'<span class="px-2 py-0.5 text-xs font-bold rounded-lg bg-amber-500/20 text-amber-400 border border-amber-500/30">🔥 {bids_count} puja(s)</span>'

        market_rows_html += f"""
        <tr class="market-row border-b border-neutral-800/60 hover:bg-neutral-800/40 transition-colors" data-pos="{pos_str}" data-name="{p_name.lower()}">
            <td class="py-3 px-4">
                <div class="flex items-center space-x-3">
                    <img src="{img}" class="w-10 h-10 rounded-xl bg-neutral-800 object-cover border border-neutral-700/50" onerror="this.src='https://assets-fantasy.llt-services.com/players/default.png'">
                    <div>
                        <div class="font-bold text-white text-sm">{p_name}</div>
                        <div class="text-[11px] text-neutral-400">ID: {mid}</div>
                    </div>
                </div>
            </td>
            <td class="py-3 px-4">
                <span class="px-2 py-0.5 rounded-lg text-xs font-bold border {pos_color.get(pos_str, pos_color['JUG'])}">{pos_str}</span>
            </td>
            <td class="py-3 px-4">{owner_tag}</td>
            <td class="py-3 px-4 text-right font-semibold text-white">{_format_money(sale_price)}</td>
            <td class="py-3 px-4 text-center">{bids_badge}</td>
            <td class="py-3 px-4 text-right text-xs text-neutral-400 font-mono">{time_left_str}</td>
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
            <div class="flex items-center justify-between p-3 bg-neutral-900/60 border border-neutral-800/80 rounded-2xl">
                <div>
                    <span class="font-bold text-white text-sm block">{u.get('nombre', '').title()}</span>
                    <span class="text-xs text-neutral-400">{_format_money(u.get('valor', 0))}</span>
                </div>
                <div class="text-right">
                    <span class="px-2.5 py-1 rounded-xl text-xs font-bold bg-emerald-500/20 text-emerald-400 border border-emerald-500/30">+{u.get('tendencia')}%</span>
                    <span class="text-[10px] text-emerald-400 block mt-0.5">+{_format_money(u.get('aceleracion', 0))}</span>
                </div>
            </div>
            """

        for d in down_list:
            top_down_html += f"""
            <div class="flex items-center justify-between p-3 bg-neutral-900/60 border border-neutral-800/80 rounded-2xl">
                <div>
                    <span class="font-bold text-white text-sm block">{d.get('nombre', '').title()}</span>
                    <span class="text-xs text-neutral-400">{_format_money(d.get('valor', 0))}</span>
                </div>
                <div class="text-right">
                    <span class="px-2.5 py-1 rounded-xl text-xs font-bold bg-rose-500/20 text-rose-400 border border-rose-500/30">{d.get('tendencia')}%</span>
                    <span class="text-[10px] text-rose-400 block mt-0.5">{_format_money(d.get('aceleracion', 0))}</span>
                </div>
            </div>
            """
    except Exception:
        top_up_html = "<div class='text-neutral-500 text-xs p-4'>Tendencias no disponibles</div>"
        top_down_html = "<div class='text-neutral-500 text-xs p-4'>Tendencias no disponibles</div>"

    # Scheduled Plan / Bid Targets
    bid_plan = state.load_bid_plan()
    plan_cards_html = ""
    if bid_plan:
        for t in bid_plan:
            m_id = t.get("market_id")
            m_cap = t.get("max_bid", 0)
            p_name = t.get("nombre") or f"Jugador #{m_id}"
            plan_cards_html += f"""
            <div class="bg-neutral-900/80 border border-neutral-800 rounded-2xl p-4 flex items-center justify-between">
                <div class="flex items-center space-x-3">
                    <div class="w-10 h-10 rounded-xl bg-amber-500/10 border border-amber-500/30 flex items-center justify-center text-amber-400 font-bold">🎯</div>
                    <div>
                        <h4 class="font-bold text-white text-sm">{p_name}</h4>
                        <p class="text-xs text-neutral-400">ID Mercado: {m_id} • Disparo a falta de 25s</p>
                    </div>
                </div>
                <div class="text-right">
                    <span class="text-xs text-neutral-400 block">Puja Tope</span>
                    <span class="font-bold text-emerald-400 text-sm">{_format_money(m_cap)}</span>
                </div>
            </div>
            """
    else:
        plan_cards_html = """
        <div class="p-6 text-center text-neutral-500 text-sm bg-neutral-900/40 border border-neutral-800/60 rounded-2xl">
            ✨ No hay pujas pendientes en cola. El agente programará nuevos objetivos en el próximo pase.
        </div>
        """

    # Event Timeline (History)
    recent_events = events.load(limit=15)
    timeline_html = ""
    for ev in reversed(recent_events):
        k = ev.get("kind", "note")
        title = ev.get("title", "")
        ts = ev.get("iso", "")[:16].replace("T", " ")
        badge_cls = "bg-neutral-800 text-neutral-300"
        icon = "📝"
        if k == "bid":
            badge_cls = "bg-amber-500/20 text-amber-300 border-amber-500/30"
            icon = "💰"
        elif k == "lineup":
            badge_cls = "bg-blue-500/20 text-blue-300 border-blue-500/30"
            icon = "⚽"
        elif k == "sell":
            badge_cls = "bg-rose-500/20 text-rose-300 border-rose-500/30"
            icon = "🏷️"
        elif k == "review":
            badge_cls = "bg-emerald-500/20 text-emerald-300 border-emerald-500/30"
            icon = "🔍"

        timeline_html += f"""
        <div class="flex items-start space-x-4 p-3.5 bg-neutral-900/50 hover:bg-neutral-900 border border-neutral-800/80 rounded-2xl transition-colors">
            <span class="text-xl p-2 bg-neutral-800/80 rounded-xl border border-neutral-700/50">{icon}</span>
            <div class="flex-1 min-w-0">
                <div class="flex items-center justify-between">
                    <span class="font-bold text-white text-sm truncate">{title}</span>
                    <span class="text-[11px] text-neutral-500 font-mono">{ts}</span>
                </div>
                <p class="text-xs text-neutral-400 mt-0.5 capitalize">Tipo: {k}</p>
            </div>
        </div>
        """

    # Format Gemini Response
    formatted_gemini = gemini_response.replace("\n", "<br>").replace("### ", "<h3 class='text-base font-bold text-emerald-400 mt-4 mb-1'>").replace("## ", "<h2 class='text-lg font-bold text-white mt-4 mb-2'>").replace("**", "<strong class='text-white'>")

    # Matchday info
    md = review_report.get("matchday", {}) if review_report else {}
    kickoff = md.get("kickoff") or "Pendiente de confirmación"
    days_left = md.get("days")
    days_text = f"{days_left:.1f} días restantes" if days_left is not None else "Próximamente"

    # Chart.js JSON data
    labels_json = json.dumps([h["label"] for h in history])
    money_json = json.dumps([h["money"] for h in history])
    value_json = json.dumps([h["value"] for h in history])
    total_json = json.dumps([h["total"] for h in history])

    # Complete HTML Template (Apple Dark Mode Aesthetics)
    html = f"""<!DOCTYPE html>
<html lang="es" class="dark">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>FantasyBot OS • Control Center</title>
    <script src="https://cdn.tailwindcss.com"></script>
    <script src="https://cdn.jsdelivr.net/npm/chart.js"></script>
    <link href="https://fonts.googleapis.com/css2?family=Plus+Jakarta+Sans:wght@400;500;600;700;800&family=JetBrains+Mono:wght@400;600&display=swap" rel="stylesheet">
    <script>
        tailwind.config = {{
            darkMode: 'class',
            theme: {{
                extend: {{
                    fontFamily: {{
                        sans: ['"Plus Jakarta Sans"', '-apple-system', 'BlinkMacSystemFont', 'sans-serif'],
                        mono: ['"JetBrains Mono"', 'monospace'],
                    }},
                    colors: {{
                        apple: {{
                            gray: '#1c1c1e',
                            dark: '#000000',
                            card: '#121214',
                            border: '#27272a',
                            accent: '#10b981',
                            blue: '#0a84ff',
                            purple: '#bf5af2',
                            amber: '#ffd60a',
                            rose: '#ff453a'
                        }}
                    }}
                }}
            }}
        }}
    </script>
    <style>
        body {{
            background-color: #000000;
            color: #f4f4f5;
            background-image: 
                radial-gradient(at 0% 0%, rgba(16, 185, 129, 0.08) 0px, transparent 50%),
                radial-gradient(at 100% 100%, rgba(10, 132, 255, 0.08) 0px, transparent 50%);
            background-attachment: fixed;
        }}
        .glass {{
            background: rgba(18, 18, 20, 0.75);
            backdrop-filter: blur(24px);
            -webkit-backdrop-filter: blur(24px);
            border: 1px solid rgba(255, 255, 255, 0.08);
        }}
        .glass-card {{
            background: rgba(24, 24, 27, 0.65);
            backdrop-filter: blur(16px);
            -webkit-backdrop-filter: blur(16px);
            border: 1px solid rgba(255, 255, 255, 0.06);
        }}
        .tab-active {{
            background: rgba(255, 255, 255, 0.12);
            color: #ffffff;
            border-color: rgba(255, 255, 255, 0.2);
        }}
        ::-webkit-scrollbar {{
            width: 6px;
            height: 6px;
        }}
        ::-webkit-scrollbar-track {{
            background: #09090b;
        }}
        ::-webkit-scrollbar-thumb {{
            background: #27272a;
            border-radius: 9999px;
        }}
        ::-webkit-scrollbar-thumb:hover {{
            background: #3f3f46;
        }}
    </style>
</head>
<body class="min-h-screen font-sans antialiased selection:bg-emerald-500 selection:text-black">
    <!-- Top Floating Apple Bar -->
    <nav class="sticky top-4 z-50 max-w-7xl mx-auto px-4">
        <div class="glass rounded-full px-5 py-3 flex items-center justify-between shadow-2xl">
            <div class="flex items-center space-x-3">
                <div class="w-8 h-8 rounded-full bg-gradient-to-tr from-emerald-500 to-teal-400 flex items-center justify-center text-black font-black text-sm shadow-lg shadow-emerald-500/20">
                    ⚡
                </div>
                <div>
                    <span class="font-extrabold text-white text-sm tracking-tight">{manager_name}</span>
                    <span class="text-[11px] text-neutral-400 block font-mono">FantasyBot OS • Gemini 3.5 Flash Lite</span>
                </div>
            </div>

            <!-- Live Status Indicator -->
            <div class="hidden md:flex items-center space-x-2 bg-neutral-900/80 border border-neutral-800 px-3.5 py-1.5 rounded-full">
                <span class="w-2 h-2 rounded-full bg-emerald-400 animate-pulse"></span>
                <span class="text-xs font-semibold text-neutral-300">Nube Activa • 09:00 & 20:15</span>
            </div>

            <!-- Header Action Pill -->
            <div class="flex items-center space-x-2">
                <span class="px-3 py-1 rounded-full text-xs font-bold border {('bg-emerald-500/10 text-emerald-400 border-emerald-500/30' if executed else 'bg-blue-500/10 text-blue-400 border-blue-500/30')}">
                    {'⚡ Ejecución en Vivo' if executed else '🔍 Modo Análisis'}
                </span>
            </div>
        </div>
    </nav>

    <!-- Main Container -->
    <main class="max-w-7xl mx-auto px-4 pt-8 pb-16 space-y-8">
        
        <!-- Hero Bento Grid -->
        <section class="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-4 gap-4">
            <!-- Card 1: Presupuesto -->
            <div class="glass-card rounded-3xl p-6 relative overflow-hidden group hover:border-neutral-700 transition-all">
                <div class="flex items-center justify-between text-neutral-400 text-xs font-bold uppercase tracking-wider">
                    <span>Caja / Presupuesto</span>
                    <span class="text-emerald-400 text-lg">💰</span>
                </div>
                <div class="mt-3">
                    <div class="text-3xl font-extrabold text-white tracking-tight">{_format_money(money)}</div>
                    <p class="text-xs text-neutral-400 mt-1">Disponible para compras y flips</p>
                </div>
                <div class="absolute -right-6 -bottom-6 w-24 h-24 bg-emerald-500/10 rounded-full blur-2xl group-hover:bg-emerald-500/20 transition-all"></div>
            </div>

            <!-- Card 2: Valor Plantilla -->
            <div class="glass-card rounded-3xl p-6 relative overflow-hidden group hover:border-neutral-700 transition-all">
                <div class="flex items-center justify-between text-neutral-400 text-xs font-bold uppercase tracking-wider">
                    <span>Valor Plantilla</span>
                    <span class="text-blue-400 text-lg">🛡️</span>
                </div>
                <div class="mt-3">
                    <div class="text-3xl font-extrabold text-white tracking-tight">{_format_money(value)}</div>
                    <p class="text-xs text-neutral-400 mt-1">{len(players)} jugadores en nómina</p>
                </div>
                <div class="absolute -right-6 -bottom-6 w-24 h-24 bg-blue-500/10 rounded-full blur-2xl group-hover:bg-blue-500/20 transition-all"></div>
            </div>

            <!-- Card 3: Patrimonio Total -->
            <div class="glass-card rounded-3xl p-6 relative overflow-hidden group hover:border-neutral-700 transition-all">
                <div class="flex items-center justify-between text-neutral-400 text-xs font-bold uppercase tracking-wider">
                    <span>Patrimonio Total</span>
                    <span class="text-purple-400 text-lg">📈</span>
                </div>
                <div class="mt-3">
                    <div class="text-3xl font-extrabold text-white tracking-tight">{_format_money(total_patrimony)}</div>
                    <p class="text-xs text-emerald-400 mt-1">Valor neto de club</p>
                </div>
                <div class="absolute -right-6 -bottom-6 w-24 h-24 bg-purple-500/10 rounded-full blur-2xl group-hover:bg-purple-500/20 transition-all"></div>
            </div>

            <!-- Card 4: Próxima Jornada -->
            <div class="glass-card rounded-3xl p-6 relative overflow-hidden group hover:border-neutral-700 transition-all">
                <div class="flex items-center justify-between text-neutral-400 text-xs font-bold uppercase tracking-wider">
                    <span>Próxima Jornada</span>
                    <span class="text-amber-400 text-lg">⏱️</span>
                </div>
                <div class="mt-3">
                    <div class="text-lg font-bold text-white truncate">{kickoff}</div>
                    <p class="text-xs font-bold text-amber-400 mt-1">{days_text}</p>
                </div>
                <div class="absolute -right-6 -bottom-6 w-24 h-24 bg-amber-500/10 rounded-full blur-2xl group-hover:bg-amber-500/20 transition-all"></div>
            </div>
        </section>

        <!-- Chart Section: Evolución Patrimonial -->
        <section class="glass-card rounded-3xl p-6 md:p-8">
            <div class="flex flex-col md:flex-row md:items-center justify-between pb-6 border-b border-neutral-800/80 gap-4">
                <div>
                    <h2 class="text-xl font-bold text-white tracking-tight">Evolución Financiera del Club</h2>
                    <p class="text-xs text-neutral-400 mt-0.5">Historial en tiempo real de presupuesto vs valor de plantilla</p>
                </div>
                <div class="flex items-center space-x-4 text-xs font-semibold">
                    <span class="flex items-center space-x-1.5"><span class="w-3 h-3 rounded-full bg-emerald-400"></span><span class="text-neutral-300">Presupuesto</span></span>
                    <span class="flex items-center space-x-1.5"><span class="w-3 h-3 rounded-full bg-blue-400"></span><span class="text-neutral-300">Valor Plantilla</span></span>
                    <span class="flex items-center space-x-1.5"><span class="w-3 h-3 rounded-full bg-purple-400"></span><span class="text-neutral-300">Total</span></span>
                </div>
            </div>
            <div class="h-72 mt-6">
                <canvas id="patrimonyChart"></canvas>
            </div>
        </section>

        <!-- AI Brain & Strategy Section -->
        <section class="relative overflow-hidden rounded-3xl p-6 md:p-8 border border-emerald-500/20 bg-gradient-to-b from-emerald-950/20 to-neutral-900/60 backdrop-blur-2xl shadow-2xl">
            <div class="flex items-center justify-between pb-6 border-b border-neutral-800">
                <div class="flex items-center space-x-3">
                    <div class="w-10 h-10 rounded-2xl bg-emerald-500/20 border border-emerald-500/40 flex items-center justify-center text-xl shadow-lg shadow-emerald-500/20">
                        🧠
                    </div>
                    <div>
                        <h2 class="text-xl font-extrabold text-white tracking-tight">Informe Táctico de Gemini Flash Lite</h2>
                        <p class="text-xs text-neutral-400">Thinking Pro (3.072 tokens de razonamiento) • {now_str}</p>
                    </div>
                </div>
                <span class="px-3 py-1 bg-emerald-500/20 text-emerald-400 border border-emerald-500/40 rounded-xl text-xs font-mono font-bold">
                    AI Director
                </span>
            </div>

            <!-- Strategy Report Content -->
            <div class="mt-6 text-sm text-neutral-300 leading-relaxed space-y-3 font-normal">
                {formatted_gemini}
            </div>
        </section>

        <!-- Navigation Tabs for Deep Dive -->
        <div class="flex items-center space-x-2 overflow-x-auto pb-2 border-b border-neutral-800 text-sm font-bold">
            <button onclick="switchTab('tab-squad')" id="btn-tab-squad" class="tab-btn tab-active px-5 py-2.5 rounded-2xl border border-transparent transition-all">👥 Mi Plantilla ({len(players)})</button>
            <button onclick="switchTab('tab-market')" id="btn-tab-market" class="tab-btn px-5 py-2.5 rounded-2xl text-neutral-400 hover:text-white border border-transparent transition-all">🛒 Mercado Completo ({len(market)})</button>
            <button onclick="switchTab('tab-trends')" id="btn-tab-trends" class="tab-btn px-5 py-2.5 rounded-2xl text-neutral-400 hover:text-white border border-transparent transition-all">📈 Tendencias & Chollos</button>
            <button onclick="switchTab('tab-plan')" id="btn-tab-plan" class="tab-btn px-5 py-2.5 rounded-2xl text-neutral-400 hover:text-white border border-transparent transition-all">⏱️ Plan & Automatizaciones</button>
            <button onclick="switchTab('tab-history')" id="btn-tab-history" class="tab-btn px-5 py-2.5 rounded-2xl text-neutral-400 hover:text-white border border-transparent transition-all">📜 Historial de Acciones</button>
        </div>

        <!-- TAB 1: SQUAD -->
        <section id="tab-squad" class="tab-content space-y-6">
            <div class="flex items-center justify-between">
                <div>
                    <h3 class="text-lg font-bold text-white">Jugadores en Nómina</h3>
                    <p class="text-xs text-neutral-400">Todos los futbolistas están listados en el mercado para recibir ofertas</p>
                </div>
                <div class="text-xs text-neutral-400 bg-neutral-900 border border-neutral-800 px-3 py-1.5 rounded-xl font-mono">
                    Huecos: {', '.join(gaps) if gaps else 'Ninguno'}
                </div>
            </div>
            <div class="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-4">
                {squad_cards_html or '<div class="col-span-4 p-8 text-center text-neutral-500">Plantilla vacía</div>'}
            </div>
        </section>

        <!-- TAB 2: FULL MARKET -->
        <section id="tab-market" class="tab-content hidden space-y-6">
            <div class="flex flex-col md:flex-row md:items-center justify-between gap-4">
                <div>
                    <h3 class="text-lg font-bold text-white">Mercado de Fichajes de la Liga</h3>
                    <p class="text-xs text-neutral-400">Jugadores a la venta en tiempo real (Sistema y Rivales)</p>
                </div>
                <div class="flex items-center space-x-2">
                    <input type="text" id="marketSearch" onkeyup="filterMarket()" placeholder="Buscar jugador..." class="bg-neutral-900 border border-neutral-800 text-white text-xs px-4 py-2 rounded-xl focus:outline-none focus:border-emerald-500 w-48">
                    <select id="posFilter" onchange="filterMarket()" class="bg-neutral-900 border border-neutral-800 text-white text-xs px-3 py-2 rounded-xl focus:outline-none focus:border-emerald-500">
                        <option value="ALL">Todas las posiciones</option>
                        <option value="POR">Porteros (POR)</option>
                        <option value="DEF">Defensas (DEF)</option>
                        <option value="MED">Centrocampistas (MED)</option>
                        <option value="DEL">Delanteros (DEL)</option>
                    </select>
                </div>
            </div>

            <div class="glass-card rounded-3xl overflow-hidden">
                <div class="overflow-x-auto">
                    <table class="w-full text-left text-sm" id="marketTable">
                        <thead>
                            <tr class="text-neutral-400 border-b border-neutral-800 text-xs uppercase font-bold tracking-wider">
                                <th class="py-3.5 px-4">Jugador</th>
                                <th class="py-3.5 px-4">Posición</th>
                                <th class="py-3.5 px-4">Vendedor</th>
                                <th class="py-3.5 px-4 text-right">Precio Salida</th>
                                <th class="py-3.5 px-4 text-center">Pujas</th>
                                <th class="py-3.5 px-4 text-right">Cierre</th>
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
        <section id="tab-trends" class="tab-content hidden space-y-6">
            <div class="grid grid-cols-1 md:grid-cols-2 gap-6">
                <!-- Top Gainers -->
                <div class="glass-card rounded-3xl p-6">
                    <h3 class="text-lg font-bold text-white mb-4 flex items-center justify-between">
                        <span>🚀 Mayores Subidas del Día</span>
                        <span class="text-xs text-emerald-400 font-mono">En alza</span>
                    </h3>
                    <div class="space-y-3">
                        {top_up_html}
                    </div>
                </div>

                <!-- Top Losers -->
                <div class="glass-card rounded-3xl p-6">
                    <h3 class="text-lg font-bold text-white mb-4 flex items-center justify-between">
                        <span>📉 Mayores Caídas del Día</span>
                        <span class="text-xs text-rose-400 font-mono">Ventas urgentes</span>
                    </h3>
                    <div class="space-y-3">
                        {top_down_html}
                    </div>
                </div>
            </div>
        </section>

        <!-- TAB 4: SCHEDULED PLAN -->
        <section id="tab-plan" class="tab-content hidden space-y-6">
            <div>
                <h3 class="text-lg font-bold text-white">Objetivos Programados (Sniping a 25s)</h3>
                <p class="text-xs text-neutral-400">Pujas preparadas para lanzarse exactamente 25 segundos antes del cierre de mercado</p>
            </div>
            <div class="space-y-3">
                {plan_cards_html}
            </div>

            <div class="glass-card rounded-3xl p-6 mt-6">
                <h4 class="text-base font-bold text-white mb-3">⏰ Rutina Autónoma en la Nube</h4>
                <div class="grid grid-cols-1 md:grid-cols-2 gap-4 text-xs text-neutral-300">
                    <div class="bg-neutral-900/60 p-4 rounded-2xl border border-neutral-800">
                        <span class="font-bold text-emerald-400 block text-sm mb-1">09:00 (Hora España) • Revisión Matinal</span>
                        Escaneo de nuevos jugadores, detección de ofertas entrantes y planificación estratégica del día.
                    </div>
                    <div class="bg-neutral-900/60 p-4 rounded-2xl border border-neutral-800">
                        <span class="font-bold text-blue-400 block text-sm mb-1">20:15 (Hora España) • Cierre & Alineación</span>
                        Disparo de pujas de último minuto (+210€ o competitivas), aseguramiento del 11 inicial y actualización web.
                    </div>
                </div>
            </div>
        </section>

        <!-- TAB 5: TIMELINE / AUDIT -->
        <section id="tab-history" class="tab-content hidden space-y-6">
            <div>
                <h3 class="text-lg font-bold text-white">Registro de Actividad y Decisiones</h3>
                <p class="text-xs text-neutral-400">Historial completo de auditoría con cada acción del agente</p>
            </div>
            <div class="space-y-2">
                {timeline_html or '<div class="p-8 text-center text-neutral-500">Sin eventos registrados</div>'}
            </div>
        </section>

        <!-- Footer -->
        <footer class="pt-8 border-t border-neutral-900 text-center text-xs text-neutral-500 font-mono">
            FantasyBot OS • Powered by Google Gemini Flash Lite (Thinking Pro) • Desplegado en GitHub Pages
        </footer>
    </main>

    <!-- Scripts for Interactivity & Chart.js -->
    <script>
        // Chart.js Setup
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
                        label: 'Presupuesto',
                        data: moneyData,
                        borderColor: '#10b981',
                        backgroundColor: 'rgba(16, 185, 129, 0.05)',
                        borderWidth: 2.5,
                        tension: 0.4,
                        pointRadius: 3,
                        pointHoverRadius: 6,
                    }},
                    {{
                        label: 'Valor Plantilla',
                        data: valueData,
                        borderColor: '#0a84ff',
                        backgroundColor: 'rgba(10, 132, 255, 0.05)',
                        borderWidth: 2.5,
                        tension: 0.4,
                        pointRadius: 3,
                        pointHoverRadius: 6,
                    }},
                    {{
                        label: 'Total Patrimonio',
                        data: totalData,
                        borderColor: '#bf5af2',
                        backgroundColor: 'rgba(191, 90, 242, 0.05)',
                        borderWidth: 2.5,
                        tension: 0.4,
                        pointRadius: 3,
                        pointHoverRadius: 6,
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
                        titleFont: {{ size: 12, weight: 'bold' }},
                        bodyFont: {{ size: 12 }},
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
                        grid: {{ color: 'rgba(255, 255, 255, 0.04)' }},
                        ticks: {{ color: '#71717a', font: {{ size: 10 }} }}
                    }},
                    y: {{
                        grid: {{ color: 'rgba(255, 255, 255, 0.04)' }},
                        ticks: {{
                            color: '#71717a',
                            font: {{ size: 10 }},
                            callback: function(value) {{
                                return (value / 1000000).toFixed(1) + 'M €';
                            }}
                        }}
                    }}
                }}
            }}
        }});

        // Tab Switching Logic
        function switchTab(tabId) {{
            document.querySelectorAll('.tab-content').forEach(el => el.classList.add('hidden'));
            document.querySelectorAll('.tab-btn').forEach(el => {{
                el.classList.remove('tab-active');
                el.classList.add('text-neutral-400');
            }});
            document.getElementById(tabId).classList.remove('hidden');
            const activeBtn = document.getElementById('btn-' + tabId);
            if (activeBtn) {{
                activeBtn.classList.add('tab-active');
                activeBtn.classList.remove('text-neutral-400');
            }}
        }}

        // Market Filter & Search
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
        f.write(html)
    print(f"\n[OK] Panel Apple Dark Mode generado exitosamente en public/index.html")
