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

    # 2. Extract all available players on the market and received offers on own players
    market_players = []
    my_received_offers = []
    for m in market:
        pm = m.get("playerMaster", {})
        pt = m.get("playerTeam", {})
        pos_id = pm.get("positionId")
        pos_str = pos_map.get(pos_id, "JUG")
        name = pm.get("nickname") or pm.get("name") or "Desconocido"
        price = m.get("salePrice") or pt.get("marketValue") or pm.get("marketValue") or 0
        mid = m.get("id")
        is_mine = str(m.get("team", {}).get("id")) == str(tid) or str(pt.get("teamId")) == str(tid)
        owner = "TU EQUIPO" if is_mine else ("SISTEMA" if m.get("discr") == "marketPlayerLeague" else "RIVAL")
        market_players.append({
            "marketId": mid,
            "nombre": name,
            "posicion": pos_str,
            "precio_salida": price,
            "propietario": owner
        })

        if is_mine:
            bids_list = m.get("bids") or m.get("offers") or []
            for b in bids_list:
                bid_id = b.get("id") or b.get("bidId") or b.get("offerId")
                amount = b.get("money") or b.get("offerMoney") or b.get("price") or 0
                buyer = b.get("user", {}).get("username") or b.get("team", {}).get("teamName") or "Sistema (Mercado)"
                my_received_offers.append({
                    "marketId": mid,
                    "offerId": bid_id,
                    "jugador": name,
                    "valor_mercado": price,
                    "oferta_recibida": amount,
                    "diferencia_pct": round(((amount - price) / price) * 100, 2) if price else 0,
                    "comprador": buyer
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

    # Build prompt with rich context (100% of original project + full market + received offers)
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
        "ofertas_recibidas_por_mis_jugadores": my_received_offers,
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
        '  "aceptar_ofertas": [\n'
        '    {"marketId": 12345, "offerId": "xyz", "jugador": "Nombre", "cantidad": 6000000}\n'
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

                # 3. Accept lucrative or requested offers received from the system/rivals
                for accept_item in decision.get("aceptar_ofertas", []):
                    m_id = accept_item.get("marketId")
                    off_id = accept_item.get("offerId")
                    money = accept_item.get("cantidad")
                    j_name = accept_item.get("jugador", str(m_id))
                    if m_id and off_id and money:
                        try:
                            fc.accept_offer(lid, m_id, off_id, int(money))
                            events.emit("sell", f"Oferta ACEPTADA por {j_name}: {int(money):,} €")
                            print(f"  ✓ Oferta ACEPTADA por {j_name}: {int(money):,} €")
                        except Exception as e:
                            print(f"  ✗ Error al aceptar oferta por {j_name}: {e}")

                # 4. Process recommended sales and ensure all owned players are listed for sale (User Guide rule)
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
            # Generate Apple-Style Dark Mode Dashboard and GitHub Summary
            from .dashboard_generator import generate_apple_dashboard
            generate_apple_dashboard(team, market, best_lineup, flips, gaps, review_report, response, decision, execute)

            # Write to GitHub Step Summary if running in GitHub Actions
            gh_summary = os.environ.get("GITHUB_STEP_SUMMARY")
            if gh_summary:
                try:
                    now_str = datetime.now().strftime("%d/%m/%Y %H:%M")
                    with open(gh_summary, "a", encoding="utf-8") as f:
                        f.write(f"## ⚽ FantasyBot OS • Informe de Mánager ({now_str})\n\n")
                        f.write(f"- **💰 Presupuesto disponible:** {team.get('teamMoney', 0):,} €\n")
                        f.write(f"- **🛡️ Valor de plantilla:** {team.get('teamValue', 0):,} €\n")
                        f.write(f"- **🎯 Huecos urgentes:** {', '.join(gaps) if gaps else 'Ninguno'}\n\n")
                        f.write(f"### 🧠 Análisis Estratégico de Gemini Flash Lite\n\n{response}\n\n")
                        f.write(f"👉 **Ver panel interactivo completo:** [https://macnogd.github.io/laligafantasybot/](https://macnogd.github.io/laligafantasybot/)\n")
                except Exception as e:
                    print(f"Error escribiendo en GITHUB_STEP_SUMMARY: {e}")

    except Exception as e:
        print(f"[ERROR] Error al llamar a Gemini: {e}")

