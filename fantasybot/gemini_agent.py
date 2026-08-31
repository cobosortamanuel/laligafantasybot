"""Gemini Agent for LALIGA Fantasy.

Uses Google Gemini (e.g. models/gemini-3.5-flash-lite) with reasoning/thinking
to autonomously manage your team, lineup, and market bids.
"""

import json
import os
import sys
import urllib.error
import urllib.request
from datetime import datetime

from .api import FantasyClient
from .sources.market_trends import market_trends
from .strategy import flip, lineup as lineup_opt, needs as needs_mod
from . import config, execute as execute_mod, events


DEFAULT_MODEL = "models/gemini-3.5-flash-lite"
GEMINI_API_URL = "https://generativelanguage.googleapis.com/v1beta/{model}:generateContent?key={key}"


def get_gemini_api_key():
    """Retrieve Gemini API key from environment variable or local config."""
    key = os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY")
    if not key:
        env_file = os.path.join(config.ROOT, ".env")
        if os.path.exists(env_file):
            with open(env_file, "r", encoding="utf-8") as f:
                for line in f:
                    if line.startswith("GEMINI_API_KEY="):
                        key = line.strip().split("=", 1)[1].strip('"').strip("'")
                        break
    return key


def call_gemini(prompt: str, system_prompt: str, api_key: str, model: str = DEFAULT_MODEL, thinking_budget: int = 3072) -> str:
    """Call Google Gemini generateContent with thinkingConfig enabled."""
    url = GEMINI_API_URL.format(model=model, key=api_key)
    
    payload = {
        "contents": [
            {
                "parts": [{"text": prompt}]
            }
        ],
        "systemInstruction": {
            "parts": [{"text": system_prompt}]
        },
        "generationConfig": {
            "thinkingConfig": {
                "thinkingBudget": thinking_budget
            },
            "temperature": 0.2
        }
    }
    
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=data,
        headers={"Content-Type": "application/json"}
    )
    
    with urllib.request.urlopen(req, timeout=60) as resp:
        res = json.loads(resp.read().decode("utf-8"))
        candidates = res.get("candidates", [])
        if not candidates:
            raise RuntimeError("No response candidates returned from Gemini.")
        parts = candidates[0].get("content", {}).get("parts", [])
        text_parts = [p.get("text", "") for p in parts if "text" in p]
        return "\n".join(text_parts).strip()


def run_gemini_manager(execute: bool = False, model: str = DEFAULT_MODEL):
    api_key = get_gemini_api_key()
    if not api_key:
        print("[ERROR] No se encontró GEMINI_API_KEY. Configúrala en tu entorno o en el archivo .env")
        return

    print("=" * 60)
    print("🤖 INICIANDO AGENTE FANTASYBOT CON GEMINI FLASH LITE (THINKING PRO)")
    print("=" * 60)

    fc = FantasyClient()
    lid, tid = fc.default_ids()
    
    print("· Obteniendo estado del equipo, informe del agente y mercado completo...")
    team = fc.team(lid, tid)
    market = fc.market(lid)
    
    # 1. Full Review Report from original agent (includes events, diffs, matchday, clause targets, sells, needs)
    review_report = {}
    try:
        from . import agent as agent_mod
        review_report = agent_mod.review(fc)
    except Exception as e:
        print(f"· Aviso al obtener review del agente: {e}")

    pos_map = {1: "POR", 2: "DEF", 3: "MED", 4: "DEL", 5: "ENT"}

    # 2. Extract all available players on the market
    market_players = []
    for m in market:
        pm = m.get("playerMaster", {})
        pt = m.get("playerTeam", {})
        pos_id = pm.get("positionId")
        pos_str = pos_map.get(pos_id, "JUG")
        name = pm.get("nickname") or pm.get("name") or "Desconocido"
        price = m.get("salePrice") or pt.get("marketValue") or pm.get("marketValue") or 0
        mid = m.get("id")
        owner = "SISTEMA" if m.get("discr") == "marketPlayerLeague" else "RIVAL"
        market_players.append({
            "marketId": mid,
            "nombre": name,
            "posicion": pos_str,
            "precio_salida": price,
            "propietario": owner
        })

    # Lineup optimization
    best_lineup = review_report.get("lineup") if review_report else None
    if not best_lineup:
        try:
            best_lineup = lineup_opt.optimize(team)
        except Exception:
            pass

    # Market opportunities (flips)
    flips = review_report.get("flips") if review_report else []
    if not flips:
        try:
            flips = [o for o in flip.opportunities(fc, lid) if o.get("margin_pct", 0) > 0][:8]
        except Exception:
            pass

    # Squad gaps
    gaps = review_report.get("gaps") if review_report else needs_mod.gaps(team)
    
    # Read existing memory
    memory_path = os.path.join(config.ROOT, "hermes", "MEMORY.md")
    existing_memory = ""
    if os.path.exists(memory_path):
        with open(memory_path, "r", encoding="utf-8") as f:
            existing_memory = f.read()

    # Build prompt with rich context (100% of original project + full market)
    situation = {
        "fecha_hora": datetime.now().isoformat(),
        "cambios_recientes_eventos": review_report.get("events", {}),
        "proxima_jornada": review_report.get("matchday", {}),
        "presupuesto_disponible": team.get("teamMoney", 0),
        "valor_plantilla": team.get("teamValue", 0),
        "huecos_en_plantilla": gaps,
        "ventas_recomendadas": review_report.get("sells", []),
        "objetivos_clausulazo": review_report.get("clause_targets", []),
        "recordatorios_programados": review_report.get("reminders", []),
        "jugadores_en_plantilla": [
            {
                "id": p["playerMaster"]["id"],
                "nombre": p["playerMaster"].get("nickname") or p["playerMaster"].get("name"),
                "posicion": pos_map.get(p["playerMaster"].get("positionId"), "-"),
                "valor": p.get("playerTeam", {}).get("marketValue") or p["playerMaster"].get("marketValue")
            } for p in team.get("players", [])
        ],
        "jugadores_disponibles_en_el_mercado": market_players,
        "alineacion_optima_calculada": best_lineup,
        "oportunidades_flip_especulacion": flips
    }

    system_prompt = (
        "Eres el Director Deportivo y Mánager de IA de un equipo en LALIGA Fantasy.\n"
        "Debes gestionar el equipo siguiendo ESTRICTAMENTE la siguiente Guía y Filosofía de Juego del Usuario:\n\n"
        "=== GUÍA FANTASY DEL USUARIO (FILOSOFÍA OBLIGATORIA) ===\n"
        "1. PRIORIDAD AL DINERO SOBRE LOS PUNTOS: Priorizar el dinero a los puntos, ya que a más dinero mejores jugadores compraremos y a la larga más puntos conseguiremos.\n"
        "2. VALOR ASCENDENTE: Priorizar siempre tener a toda la plantilla con valor de mercado en subida.\n"
        "3. JUGADORES EN VENTA: Mantener a los jugadores en venta en el mercado para recibir ofertas diarias del sistema, evaluar ofertas interesantes y vigilar caídas de precio.\n"
        "4. ALINEACIÓN Y DIFICULTAD DEL RIVAL: Para la alineación, evaluar probabilidades de titularidad cruzadas con el rival al que se enfrentan (si juegan contra un grande como Barcelona/Real Madrid harán menos puntos, y si juegan contra rivales débiles más puntos).\n"
        "5. PROTECCIÓN DE CLÁUSULAS (14 DÍAS): Las cláusulas duran 14 días exactos tras la compra. Antes de que se le acabe la cláusula a un jugador valioso es interesante venderlo para no perderlo por clausulazo rival.\n"
        "6. TRADING Y RENTABILIDAD PORCENTUAL (ROI %): Evaluar siempre la rentabilidad porcentual sobre el capital invertido, no solo la subida absoluta. Una oferta recibida debe compararse con el beneficio esperado de mantener al jugador.\n"
        "7. MERCADO Y PUJAS:\n"
        "   - Solo hacer ofertas a jugadores que estén subiendo o que hayan hecho muchos puntos en la última jornada por lo que pueden subir.\n"
        "   - Priorizar los jugadores que estén subiendo y que sean caros (a mayor precio, mayor oscilación).\n"
        "   - Si un jugador NO tiene pujas, lo suyo es ficharlo al precio de mercado para que salga barato, o como mucho subir unos 210 € por si acaso alguien quiere pujar en el último segundo.\n"
        "   - Si un jugador cotizado ya tiene puja/competencia, subirla un poco; si sube mucho, subirla algo más, pero NUNCA pujar mucho más de lo que vale.\n"
        "   - Si un jugador ya ha subido mucho, no comprar por inercia; evaluar subida restante vs riesgo de corrección.\n"
        "   - Nunca gastar toda la caja salvo oportunidad excepcional.\n"
        "8. RIVALES Y CLAUSULAZOS:\n"
        "   - Nunca hacer ofertas directas a rivales.\n"
        "   - Los clausulazos son clave: si un rival tiene un jugador con cláusula igual o poco superior al mercado y está subiendo mucho, comprarlo. Si es caro y sube, comprarlo para ponerlo en venta y generar beneficios.\n"
        "   - 24 horas antes del inicio de la jornada NO se pueden hacer clausulazos. Tener siempre la plantilla elegida antes de que empiece la jornada.\n"
        "=======================================================\n\n"
        "ESTRUCTURA DE RESPUESTA:\n"
        "1. Análisis Estratégico aplicando la Guía del Usuario.\n"
        "2. Recomendaciones Concretas y desglose de movimientos.\n"
        "3. Bloque JSON final estricto:\n"
        "```json\n"
        "{\n"
        '  "aplicar_alineacion": true,\n'
        '  "pujas_recomendadas": [\n'
        '    {"marketId": 123888363, "nombre": "Dmitrovic", "puja_maxima": 43000000}\n'
        "  ],\n"
        '  "ventas_recomendadas": [\n'
        '    {"playerId": 12345, "nombre": "Jugador", "precio_venta": 5000000}\n'
        "  ],\n"
        '  "nueva_memoria": "Breve nota actualizada para recordar en futuras revisiones."\n'
        "}\n"
        "```"
    )

    user_prompt = (
        f"Memoria previa del mánager:\n{existing_memory}\n\n"
        f"Estado actual de la liga, equipo y mercado completo:\n{json.dumps(situation, ensure_ascii=False, indent=2)}\n\n"
        "Aplica estrictamente la Guía Fantasy del Usuario, razona profundamente y decide qué fichajes y movimientos debemos realizar hoy."
    )

    print("· Consultando a Gemini 3.5 Flash Lite (Thinking activado)...")
    try:
        response = call_gemini(user_prompt, system_prompt, api_key, model=model)
        print("\n" + "=" * 60)
        print("🧠 DECISIÓN DEL MÁNAGER GEMINI:")
        print("=" * 60)
        print(response)
        print("=" * 60)

        events.emit("note", "🧠 Gemini Manager: Análisis estratégico y decisiones", detail={"analisis": response})

        # Parse JSON decision
        if "```json" in response:
            json_str = response.split("```json")[1].split("```")[0].strip()
            decision = json.loads(json_str)

            # Update memory
            if decision.get("nueva_memoria"):
                os.makedirs(os.path.dirname(memory_path), exist_ok=True)
                with open(memory_path, "w", encoding="utf-8") as f:
                    f.write(f"# MEMORY\n\nÚltima actualización: {datetime.now().strftime('%Y-%m-%d %H:%M')}\n\n{decision['nueva_memoria']}\n")
                print("\n[OK] Memoria persistente actualizada en hermes/MEMORY.md")

            # Execute actions if requested
            if execute:
                print("\n⚡ MODO EJECUCIÓN ACTIVO: Aplicando decisiones en LaLiga Fantasy...")
                from . import bidding, state

                if decision.get("aplicar_alineacion") and best_lineup and not best_lineup.get("incomplete"):
                    try:
                        current_ids = []
                        execute_mod.apply_lineup(fc, tid, best_lineup, current_ids, dry_run=False)
                        print("  ✓ Alineación óptima aplicada en el juego.")
                    except Exception as e:
                        print(f"  ✗ No se pudo aplicar alineación: {e}")

                # 1. Register recommended bids in the Last-Minute Sniping Bid Plan
                for bid_item in decision.get("pujas_recomendadas", []):
                    m_id = bid_item.get("marketId")
                    max_bid = bid_item.get("puja_maxima")
                    nombre = bid_item.get("nombre", str(m_id))
                    if m_id and max_bid:
                        state.add_bid_target(str(m_id), int(max_bid), nombre=nombre)
                        print(f"  🎯 Objetivo añadido al Plan de Pujas de Último Minuto: {nombre} (Tope: {int(max_bid):,} €)")
                
                # 2. Execute last-minute bids using the bidding engine (respects +210 EUR rule and close timing)
                try:
                    bidding.run_bid_plan(lid, dry_run=False)
                except Exception as e:
                    print(f"  ✗ Aviso en el motor de pujas de último minuto: {e}")

                # 3. Process recommended sales and ensure all owned players are listed for sale (User Guide rule)
                for p in team.get("players", []):
                    pm = p.get("playerMaster", {})
                    pt = p.get("playerTeam", {})
                    p_id = pm.get("id")
                    m_val = pt.get("marketValue") or pm.get("marketValue") or 1000000
                    p_name = pm.get("nickname") or pm.get("name")
                    try:
                        fc.sell_player(lid, p_id, int(m_val))
                    except Exception:
                        pass  # already on sale or not sellable

                for sell_item in decision.get("ventas_recomendadas", []):
                    p_id = sell_item.get("playerId")
                    price = sell_item.get("precio_venta")
                    if p_id and price:
                        try:
                            fc.sell_player(lid, p_id, int(price))
                            events.emit("sell", f"Puesto a la venta: {sell_item.get('nombre', p_id)} ({int(price):,} €)")
                            print(f"  ✓ Puesto a la venta {sell_item.get('nombre', p_id)} por {int(price):,} €")
                        except Exception as e:
                            print(f"  ✗ Error al vender {sell_item.get('nombre', p_id)}: {e}")
            else:
                print("\n(Modo simulación: no se han enviado cambios a LaLiga Fantasy. Usa --execute para aplicarlos).")
            # Generate HTML Dashboard and GitHub Summary
            generate_dashboard(team, best_lineup, flips, gaps, response, decision, execute)

    except Exception as e:
        print(f"[ERROR] Error al llamar a Gemini: {e}")


def generate_dashboard(team, best_lineup, flips, gaps, response_text, decision, executed):
    """Generates a responsive static HTML dashboard in public/index.html and writes to GITHUB_STEP_SUMMARY."""
    now_str = datetime.now().strftime("%d/%m/%Y %H:%M")
    money = team.get("teamMoney", 0)
    value = team.get("teamValue", 0)
    
    players = team.get("players", [])
    pos_map = {1: "POR", 2: "DEF", 3: "MED", 4: "DEL", 5: "ENT"}

    players_rows = ""
    for p in players:
        pm = p.get("playerMaster", {})
        pt = p.get("playerTeam", {})
        p_name = pm.get("nickname") or pm.get("name") or "Desconocido"
        p_pos = pos_map.get(pm.get("positionId"), "-")
        p_val = pt.get("marketValue") or pm.get("marketValue") or 0
        players_rows += f"""
        <tr class="border-b border-gray-800 hover:bg-gray-800/40">
            <td class="py-2 px-3 font-semibold text-emerald-400">{p_pos}</td>
            <td class="py-2 px-3">{p_name}</td>
            <td class="py-2 px-3 text-right text-gray-300">{p_val:,} €</td>
        </tr>
        """

    flips_rows = ""
    for f in flips:
        flips_rows += f"""
        <tr class="border-b border-gray-800 hover:bg-gray-800/40">
            <td class="py-2 px-3 font-medium text-white">{f.get('nombre', '-')}</td>
            <td class="py-2 px-3 text-right text-gray-300">{f.get('precio', 0):,} €</td>
            <td class="py-2 px-3 text-right text-emerald-400 font-semibold">+{f.get('margen_pct', 0)}%</td>
        </tr>
        """

    # Format reasoning markdown-like to HTML
    clean_report = response_text.replace("\n", "<br>").replace("### ", "<h3 class='text-lg font-bold text-emerald-400 mt-3 mb-1'>").replace("## ", "<h2 class='text-xl font-bold text-white mt-4 mb-2'>").replace("**", "<strong class='text-white'>")

    html = f"""<!DOCTYPE html>
<html lang="es" class="dark">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>FantasyBot - Panel de Control</title>
    <script src="https://cdn.tailwindcss.com"></script>
    <link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;600;700&display=swap" rel="stylesheet">
    <style>
        body {{ font-family: 'Inter', sans-serif; }}
    </style>
</head>
<body class="bg-gray-950 text-gray-100 min-h-screen p-4 md:p-8">
    <div class="max-w-5xl mx-auto space-y-6">
        <!-- Header -->
        <header class="flex flex-col md:flex-row justify-between items-start md:items-center bg-gray-900 border border-gray-800 rounded-2xl p-6 shadow-xl">
            <div>
                <div class="flex items-center space-x-3">
                    <span class="text-3xl">⚽</span>
                    <h1 class="text-2xl md:text-3xl font-bold text-white">FantasyBot Manager</h1>
                </div>
                <p class="text-sm text-gray-400 mt-1">IA: Gemini 3.5 Flash Lite (Thinking) | Actualizado: {now_str}</p>
            </div>
            <div class="flex items-center space-x-2 mt-4 md:mt-0">
                <span class="px-3 py-1 bg-emerald-500/20 text-emerald-400 border border-emerald-500/40 rounded-full text-xs font-bold uppercase tracking-wider">
                    {'⚡ Ejecutado en Juego' if executed else '🔍 Modo Análisis'}
                </span>
            </div>
        </header>

        <!-- Stats Cards -->
        <div class="grid grid-cols-1 sm:grid-cols-3 gap-4">
            <div class="bg-gray-900 border border-gray-800 rounded-xl p-5">
                <span class="text-xs text-gray-400 font-semibold uppercase tracking-wider">Presupuesto Disponible</span>
                <p class="text-2xl md:text-3xl font-bold text-emerald-400 mt-1">{money:,} €</p>
            </div>
            <div class="bg-gray-900 border border-gray-800 rounded-xl p-5">
                <span class="text-xs text-gray-400 font-semibold uppercase tracking-wider">Valor de Plantilla</span>
                <p class="text-2xl md:text-3xl font-bold text-blue-400 mt-1">{value:,} €</p>
            </div>
            <div class="bg-gray-900 border border-gray-800 rounded-xl p-5">
                <span class="text-xs text-gray-400 font-semibold uppercase tracking-wider">Huecos Urgentes</span>
                <p class="text-xl font-bold text-amber-400 mt-1">{', '.join(gaps) if gaps else 'Plantilla completa'}</p>
            </div>
        </div>

        <!-- Gemini Tactical Analysis -->
        <div class="bg-gray-900 border border-gray-800 rounded-2xl p-6 shadow-lg">
            <div class="flex items-center space-x-2 border-b border-gray-800 pb-3 mb-4">
                <span class="text-2xl">🧠</span>
                <h2 class="text-xl font-bold text-white">Informe Estratégico de Gemini</h2>
            </div>
            <div class="text-gray-300 leading-relaxed text-sm md:text-base space-y-2">
                {clean_report}
            </div>
        </div>

        <!-- Squad & Market Tables -->
        <div class="grid grid-cols-1 md:grid-cols-2 gap-6">
            <!-- Plantilla -->
            <div class="bg-gray-900 border border-gray-800 rounded-2xl p-6 shadow-lg">
                <h3 class="text-lg font-bold text-white mb-3 flex items-center justify-between">
                    <span>👥 Tu Plantilla ({len(players)} jugadores)</span>
                </h3>
                <div class="overflow-x-auto">
                    <table class="w-full text-left text-sm">
                        <thead>
                            <tr class="text-gray-400 border-b border-gray-800 text-xs uppercase">
                                <th class="py-2 px-3">Pos</th>
                                <th class="py-2 px-3">Jugador</th>
                                <th class="py-2 px-3 text-right">Valor</th>
                            </tr>
                        </thead>
                        <tbody>
                            {players_rows}
                        </tbody>
                    </table>
                </div>
            </div>

            <!-- Flips -->
            <div class="bg-gray-900 border border-gray-800 rounded-2xl p-6 shadow-lg">
                <h3 class="text-lg font-bold text-white mb-3 flex items-center justify-between">
                    <span>📈 Oportunidades de Reventa (Flips)</span>
                </h3>
                <div class="overflow-x-auto">
                    <table class="w-full text-left text-sm">
                        <thead>
                            <tr class="text-gray-400 border-b border-gray-800 text-xs uppercase">
                                <th class="py-2 px-3">Jugador</th>
                                <th class="py-2 px-3 text-right">Precio</th>
                                <th class="py-2 px-3 text-right">Margen</th>
                            </tr>
                        </thead>
                        <tbody>
                            {flips_rows or '<tr><td colspan="3" class="py-4 text-center text-gray-500">Sin flips claros</td></tr>'}
                        </tbody>
                    </table>
                </div>
            </div>
        </div>

        <footer class="text-center text-xs text-gray-500 py-4">
            FantasyBot Autonomous Cloud Agent • Desplegado automáticamente en GitHub Pages
        </footer>
    </div>
</body>
</html>
    """
    
    # Write to public/index.html
    public_dir = os.path.join(config.ROOT, "public")
    os.makedirs(public_dir, exist_ok=True)
    with open(os.path.join(public_dir, "index.html"), "w", encoding="utf-8") as f:
        f.write(html)
    print(f"\n[OK] Panel web generado en public/index.html")

    # Write to GitHub Step Summary if running in GitHub Actions
    gh_summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if gh_summary:
        try:
            with open(gh_summary, "a", encoding="utf-8") as f:
                f.write(f"## ⚽ FantasyBot - Informe de Mánager ({now_str})\n\n")
                f.write(f"- **Presupuesto disponible:** {money:,} €\n")
                f.write(f"- **Valor de plantilla:** {value:,} €\n")
                f.write(f"- **Huecos:** {', '.join(gaps) if gaps else 'Ninguno'}\n\n")
                f.write(f"### 🧠 Análisis de Gemini Flash Lite\n\n{response_text}\n")
        except Exception as e:
            print(f"Error escribiendo en GITHUB_STEP_SUMMARY: {e}")

