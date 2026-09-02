"""Gemini Agent for FantasyBot.

Uses Gemini 3.5 Flash Lite with Thinking capability to analyze:
- Clean system market (free agents).
- Full rival rosters with buyout clauses, shield expiry countdowns, and market trends.
- Owned squad listings and received offers.
- Optimal lineup and points strategy.
Executes immediate buyouts, last-minute market snipes, and accepts profitable offers.
"""

import json
import os
from datetime import datetime, timezone, timedelta
import urllib.request
import urllib.error

from . import config, events, execute as execute_mod, state
from .strategy import flip, lineup as lineup_opt, needs as needs_mod
from .api import FantasyClient
from .sources.lineups import probable_lineups
from .sources.market_trends import market_trends

try:
    from zoneinfo import ZoneInfo
    SPAIN_TZ = ZoneInfo("Europe/Madrid")
except Exception:
    SPAIN_TZ = timezone(timedelta(hours=2))


def call_gemini(prompt: str, system_instruction: str, api_key: str, model: str = "gemini-flash-lite-latest") -> str:
    """Calls Gemini REST API with automatic model fallback."""
    candidate_models = [model, "gemini-flash-lite-latest", "gemini-flash-latest"]
    last_err = None

    payload = {
        "contents": [
            {
                "parts": [
                    {"text": prompt}
                ]
            }
        ],
        "systemInstruction": {
            "parts": [
                {"text": system_instruction}
            ]
        },
        "generationConfig": {
            "temperature": 0.2,
            "maxOutputTokens": 8192
        }
    }

    for m in candidate_models:
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{m}:generateContent?key={api_key}"
        req = urllib.request.Request(
            url,
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"}
        )
        try:
            with urllib.request.urlopen(req, timeout=60) as resp:
                data = json.loads(resp.read().decode("utf-8"))
            candidates = data.get("candidates", [])
            if candidates:
                parts = candidates[0].get("content", {}).get("parts", [])
                text_parts = [p.get("text", "") for p in parts if "text" in p]
                return "".join(text_parts)
        except Exception as e:
            last_err = e
            continue

    raise last_err or ValueError("Failed to call Gemini API with candidate models.")


def run_gemini_agent(execute: bool = False, model: str = "gemini-flash-lite-latest"):
    """Runs the full Gemini AI manager review and execution cycle."""
    api_key = os.environ.get("GEMINI_API_KEY")
    if not api_key:
        env_path = os.path.join(config.ROOT, ".env")
        if os.path.exists(env_path):
            try:
                with open(env_path, "r", encoding="utf-8") as f:
                    for line in f:
                        if line.startswith("GEMINI_API_KEY="):
                            api_key = line.split("=", 1)[1].strip().strip('"').strip("'")
                            break
            except Exception:
                pass

    if not api_key:
        print("[ERROR] GEMINI_API_KEY no encontrada en las variables de entorno.")
        return

    now_spain = datetime.now(SPAIN_TZ)
    now_utc = datetime.now(timezone.utc)
    now_spain_str = now_spain.strftime("%A, %d de %B de %Y a las %H:%M:%S (Hora España)")

    print("=" * 60)
    print("🤖 INICIANDO AGENTE FANTASYBOT CON GEMINI FLASH LITE")
    print(f"🕒 Fecha y Hora: {now_spain_str}")
    print("=" * 60)

    fc = FantasyClient()
    lid, tid = fc.default_ids()
    
    print("· Obteniendo estado del equipo, mercado y plantillas rivales...")
    team = fc.team(lid, tid)
    market = fc.market(lid)
    league_teams = fc.league_teams(lid)
    
    # 1. Deterministic Squad Auto-Listing (Poner siempre a toda la plantilla en el mercado a precio de mercado)
    market_player_ids = set()
    for m in market:
        pm = m.get("playerMaster", {})
        if pm.get("id"):
            market_player_ids.add(str(pm.get("id")))

    print("· Comprobando estado de venta de la plantilla en el mercado...")
    for p in team.get("players", []):
        pm = p.get("playerMaster", {})
        pt_id = p.get("playerTeamId")
        p_name = pm.get("nickname") or pm.get("name") or "Desconocido"
        m_val = pm.get("marketValue") or 0
        already_listed = p.get("playerMarket") is not None
        if pt_id and not already_listed and m_val > 0:
            try:
                fc.sell_player(lid, pt_id, int(m_val))
                print(f"  ✓ Auto-listado en mercado: {p_name} por {int(m_val):,} € (Precio de mercado)")
                events.emit("sell", f"Puesto a la venta: {p_name} ({int(m_val):,} €)")
            except Exception as e:
                print(f"  · Info al listar {p_name}: {e}")

    # Re-fetch market after auto-listing to have fresh state
    try:
        market = fc.market(lid)
    except Exception:
        pass

    # 2. Matchday and Review report
    review_report = {}
    try:
        from . import agent as agent_mod
        review_report = agent_mod.review(fc)
    except Exception as e:
        print(f"· Aviso al obtener review del agente: {e}")

    pos_map = {1: "POR", 2: "DEF", 3: "MED", 4: "DEL", 5: "ENT"}

    # 3. Probable lineups and Market trends indexes
    prob_index = {}
    try:
        prob_index = probable_lineups()
    except Exception:
        pass

    t_index = {}
    try:
        from .sources.market_trends import trends_index
        t_index = trends_index()
    except Exception:
        pass

    from .matching import match_name

    def get_player_trend(pm):
        nick = pm.get("nickname") or ""
        name = pm.get("name") or ""
        if not t_index:
            return {"en_subida": True, "tendencia": "ESTABLE", "variacion_diaria": 0}
        t = match_name(nick, name, t_index)
        if not t:
            return {"en_subida": True, "tendencia": "ESTABLE (Sin datos)", "variacion_diaria": 0}
        val = t.get("valor", 0)
        val1 = t.get("valor1", val)
        diff = val - val1
        tend_num = t.get("tendencia", 0)
        is_falling = (diff < 0) or (tend_num < 0)
        if is_falling:
            tend_str = f"BAJANDO ({diff:+,} €/día)"
        elif diff > 0 or tend_num > 0:
            tend_str = f"SUBIENDO ({diff:+,} €/día)"
        else:
            tend_str = "ESTABLE"
        return {
            "en_subida": not is_falling,
            "tendencia": tend_str,
            "variacion_diaria": diff
        }

    # 4. Separate System Free Agent Market vs Received Offers
    mercado_libre_sistema = []
    my_received_offers = []

    for m in market:
        pm = m.get("playerMaster", {})
        pt = m.get("playerTeam", {})
        pos_id = pm.get("positionId")
        pos_str = pos_map.get(pos_id, "JUG")
        name = pm.get("nickname") or pm.get("name") or "Desconocido"
        price = m.get("salePrice") or pt.get("marketValue") or pm.get("marketValue") or 0
        mid = m.get("id")
        is_mine = (
            str(m.get("sellerTeam", {}).get("id")) == str(tid)
            or str(m.get("team", {}).get("id")) == str(tid)
            or str(pt.get("teamId")) == str(tid)
        )
        is_system = (m.get("discr") == "marketPlayerLeague")

        # Starting probability & trend
        prob = None
        if prob_index:
            minfo = match_name(pm.get("nickname", ""), pm.get("name", ""), prob_index)
            if minfo:
                prob = minfo.get("prob")

        trend_info = get_player_trend(pm)

        if is_system:
            mercado_libre_sistema.append({
                "marketId": mid,
                "playerId": pm.get("id"),
                "nombre": name,
                "posicion": pos_str,
                "precio_salida": price,
                "pujas_actuales": m.get("numberOfBids", 0),
                "prob_titular": prob,
                "en_subida": trend_info["en_subida"],
                "tendencia": trend_info["tendencia"],
                "variacion_diaria": trend_info["variacion_diaria"],
                "puntos": pm.get("points", 0),
                "media": pm.get("averagePoints", 0),
                "cierre": m.get("expirationDate")
            })

    # Fetch active received offers for all players in squad
    for p in team.get("players", []):
        pm = p.get("playerMaster", {})
        ptid = p.get("playerTeamId")
        pname = pm.get("nickname") or pm.get("name") or "Desconocido"
        val = pm.get("marketValue") or 0
        mid = p.get("playerMarket", {}).get("id")
        if ptid and p.get("playerMarket"):
            try:
                offers = fc.player_offers(lid, ptid)
                if isinstance(offers, list):
                    for off in offers:
                        if off.get("status") == "pending":
                            off_id = off.get("id")
                            amt = off.get("money") or 0
                            diff_pct = round(((amt - val) / val) * 100, 2) if val else 0
                            buyer = "Sistema (Mercado)" if off.get("isFromMarket") else "Rival"
                            my_received_offers.append({
                                "playerTeamId": ptid,
                                "marketId": mid,
                                "offerId": off_id,
                                "jugador": pname,
                                "valor_mercado": val,
                                "oferta_recibida": amt,
                                "diferencia_pct": diff_pct,
                                "comprador": buyer,
                                "expiracion": off.get("expirationDate")
                            })
            except Exception:
                pass

    # 5. Extract Full Rival Rosters with Buyout Clauses & Shield Status
    rival_clause_targets = []
    player_to_team_id = {}
    for lt in league_teams:
        for p in lt.get("players", []):
            pm = p.get("playerMaster", {})
            ptid = p.get("playerTeamId")
            if ptid:
                player_to_team_id[str(ptid)] = ptid
                if pm.get("id"):
                    player_to_team_id[str(pm.get("id"))] = ptid
        if str(lt.get("id")) == str(tid):
            continue
        manager_name = lt.get("manager", {}).get("managerName") or lt.get("teamName") or "Rival"
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
            seconds_to_open = 0
            locked_until_str = "Abierta"
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
                        locked_until_str = f"En {hours}h {mins}m ({exp_dt.strftime('%d/%m %H:%M')})"
                except Exception:
                    pass

            prob = None
            if prob_index:
                minfo = match_name(pm.get("nickname", ""), pm.get("name", ""), prob_index)
                if minfo:
                    prob = minfo.get("prob")

            trend_info = get_player_trend(pm)

            rival_clause_targets.append({
                "playerId": p_id,
                "nombre": name,
                "posicion": pos_str,
                "equipo_rival": manager_name,
                "valor_mercado": val,
                "clausula": int(clause),
                "ratio_clausula_valor": round(clause / val, 2) if val else 0,
                "clausula_abierta": is_open,
                "segundos_para_abrir": seconds_to_open,
                "estado_escudo": locked_until_str,
                "prob_titular": prob,
                "en_subida": trend_info["en_subida"],
                "tendencia": trend_info["tendencia"],
                "variacion_diaria": trend_info["variacion_diaria"],
                "puntos": pm.get("points", 0),
                "media": pm.get("averagePoints", 0),
                "estado_medico": pm.get("playerStatus", "ok")
            })

    # Lineup optimization
    best_lineup = review_report.get("lineup") if review_report else None
    if not best_lineup or "payload" not in best_lineup:
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

    # Calculate projected budget taking into account scheduled last-minute bids
    current_money = team.get("teamMoney", 0)
    scheduled_bids = state.load_bid_plan()
    dinero_comprometido_en_pujas = sum(int(t.get("max_bid", 0)) for t in scheduled_bids)
    presupuesto_proyectado = current_money - dinero_comprometido_en_pujas

    # Matchday timing & Buyout restriction status
    matchday_info = review_report.get("matchday", {})
    kickoff_iso = matchday_info.get("kickoff")
    hours_to_kickoff = None
    veto_24h_activo = False
    permitido_clausulazo_inmediato = True
    estado_regla_clausulas = "PERMITIDO (Faltan más de 24h para el inicio de la jornada)"

    if kickoff_iso:
        try:
            k_dt = datetime.fromisoformat(kickoff_iso)
            if k_dt.tzinfo is None:
                k_dt = k_dt.replace(tzinfo=timezone.utc)
            seconds_to_k = (k_dt - now_utc).total_seconds()
            hours_to_kickoff = round(seconds_to_k / 3600.0, 1)

            if 0 < seconds_to_k <= (24 * 3600):
                veto_24h_activo = True
                permitido_clausulazo_inmediato = False
                estado_regla_clausulas = f"BLOQUEADO POR REGLA 24H (Faltan solo {hours_to_kickoff}h para el kickoff). Las cláusulas se reabrirán al arrancar la jornada."
            elif seconds_to_k <= 0:
                veto_24h_activo = False
                permitido_clausulazo_inmediato = True
                estado_regla_clausulas = "PERMITIDO (La jornada ya ha comenzado / en curso. Cláusulas desbloqueadas para la siguiente fecha)."
            else:
                veto_24h_activo = False
                permitido_clausulazo_inmediato = True
                estado_regla_clausulas = f"TOTALMENTE PERMITIDO (Margen amplio de {hours_to_kickoff}h antes del kickoff; fuera de la restricción de 24h)."
        except Exception:
            pass

    # Build prompt situation
    situation = {
        "fecha_hora_actual_espana": now_spain_str,
        "proxima_jornada": matchday_info,
        "normativa_clausulazos": {
            "horas_para_inicio_jornada": hours_to_kickoff,
            "veto_24h_activo_ahora": veto_24h_activo,
            "permitido_pagar_clausulazos_inmediatos_ahora": permitido_clausulazo_inmediato,
            "diagnostico": estado_regla_clausulas,
            "reapertura_clausulas": "Al arrancar el primer partido de la jornada (kickoff), las cláusulas se desbloquean nuevamente."
        },
        "presupuesto_actual_en_caja": current_money,
        "dinero_comprometido_en_pujas_programadas": dinero_comprometido_en_pujas,
        "presupuesto_disponible_proyectado": presupuesto_proyectado,
        "valor_plantilla": team.get("teamValue", 0),
        "huecos_en_plantilla": gaps,
        "mi_plantilla": [
            {
                "id": p["playerMaster"]["id"],
                "nombre": p["playerMaster"].get("nickname") or p["playerMaster"].get("name"),
                "posicion": pos_map.get(p["playerMaster"].get("positionId"), "-"),
                "valor": p.get("playerTeam", {}).get("marketValue") or p["playerMaster"].get("marketValue"),
                "puntos": p["playerMaster"].get("points", 0),
                "en_subida": get_player_trend(p["playerMaster"])["en_subida"],
                "tendencia": get_player_trend(p["playerMaster"])["tendencia"]
            } for p in team.get("players", [])
        ],
        "ofertas_recibidas_por_mis_jugadores": my_received_offers,
        "mercado_libre_sistema": mercado_libre_sistema,
        "jugadores_rivales_y_clausulazos": rival_clause_targets,
        "acciones_programadas_activas": scheduled_bids,
        "recordatorios_activos": state.load_reminders(),
        "alineacion_optima_calculada": best_lineup,
        "oportunidades_flip_especulacion": flips
    }

    system_prompt = (
        "Eres el Director Deportivo y Mánager de IA de un equipo en LALIGA Fantasy.\n"
        f"Fecha y hora actual: {now_spain_str}.\n"
        "Debes gestionar el equipo siguiendo ESTRICTAMENTE la siguiente Guía y Filosofía de Juego del Usuario:\n\n"
        "=== GUÍA FANTASY DEL USUARIO (FILOSOFÍA OBLIGATORIA) ===\n"
        "1. PRIORIDAD AL DINERO SOBRE LOS PUNTOS: Priorizar el dinero a los puntos en el corto/medio plazo para construir un gran patrimonio.\n"
        "2. CONTROL DE PRESUPUESTO ACTUAL Y PROYECTADO:\n"
        "   - Tienes el presupuesto en caja (`presupuesto_actual_en_caja`), el dinero ya reservado para compras de mercado (`dinero_comprometido_en_pujas_programadas`) y el saldo neto restante (`presupuesto_disponible_proyectado`).\n"
        "   - Al decidir nuevas compras o clausulazos, evalúa siempre el `presupuesto_disponible_proyectado` para no exceder los fondos reales.\n"
        "3. VALOR ASCENDENTE (REGLA DE ORO):\n"
        "   - Priorizar tener a toda la plantilla con valor de mercado en subida (`en_subida: true`).\n"
        "   - PROHIBICIÓN ABSOLUTA: NUNCA pujar ni clausular a ningún jugador con `en_subida: false` o tendencia `BAJANDO`, por muchos puntos o media alta que tenga (ej. Mikautadze u otros jugadores en caída). Comprar jugadores perdiendo valor destruye el patrimonio del club.\n"
        "   - SOLO comprar o clausular jugadores con `en_subida: true` (tendencia alcista) o con valor de mercado completamente estable.\n"
        "4. JUGADORES EN VENTA: Todos los jugadores están siempre en el mercado para recibir ofertas diarias de la máquina y monetizar picos de valor.\n"
        "5. ALINEACIÓN Y DIFICULTAD DEL RIVAL: Evaluar probabilidades de titularidad y dificultad del partido (los partidos difíciles reducen la puntuación esperada).\n"
        "6. PROTECCIÓN DE CLÁUSULAS (14 DÍAS): El escudo de protección dura 14 días. Antes de que expire la cláusula de un jugador cotizado, evaluar venderlo o protegerlo si hay riesgo de robo rival.\n"
        "7. TRADING Y RENTABILIDAD PORCENTUAL (ROI %): Evaluar siempre la rentabilidad porcentual sobre el capital invertido.\n"
        "8. MERCADO LIBRE Y PUJAS:\n"
        "   - Solo pujar por jugadores que estén subiendo (`en_subida: true`) o rindan de forma sobresaliente.\n"
        "   - Si un jugador NO tiene pujas, pujar a su PRECIO DE MERCADO o sumar exactamente +210 € como margen de seguridad.\n"
        "   - Si ya tiene pujas rivales, subir de forma moderada sin sobrepagar.\n"
        "   - NUNCA gastar toda la caja salvo oportunidad irrepetible.\n"
        "9. RIVALES Y CLAUSULAZOS (MUY IMPORTANTE - REGLAS DE TIEMPO Y VALOR):\n"
        "   - NUNCA hacer pujas normales a jugadores de rivales (solo mercado libre o clausulazos).\n"
        "   - SOLO CLAUSULAR JUGADORES EN SUBIDA: Exclusivamente se permite pagar o programar cláusulas de jugadores con `en_subida: true`.\n"
        "   - CLAUSULAZOS DIRECTOS (PAGO INMEDIATO): Si `permitido_pagar_clausulazos_inmediatos_ahora` es TRUE, y un jugador rival tiene su cláusula ABIERTA, está en SUBIDA (`en_subida: true`) y es rentable (o cubre huecos críticos como POR/DEF), EJECUTA EL CLAUSULAZO INMEDIATO sin dudar.\n"
        "   - REGLA DE LAS 24 HORAS PREVIAS AL KICKOFF:\n"
        "     * Si `horas_para_inicio_jornada > 24`: LUZ VERDE TOTAL. NUNCA te abstengas de clausular por miedo a la jornada si faltan más de 24 horas (ej. faltan 30h, 54h o días).\n"
        "     * Si `0 < horas_para_inicio_jornada <= 24`: En esta ventana exacta de 24h previas al primer partido, LaLiga Fantasy bloquea el pago de cláusulas para proteger las alineaciones de la jornada. En ese caso NO se pagan cláusulas inmediatas, pero SÍ se pueden programar para la reapertura.\n"
        "     * REAPERTURA AL INICIAR LA JORNADA: En cuanto empieza el primer partido (kickoff), los clausulazos se vuelven a DESBLOQUEAR automáticamente en el juego.\n"
        "   - CLAUSULAZOS PROGRAMADOS: Si el escudo de un jugador rival vence pronto (pocas horas/días) Y ESTÁ EN SUBIDA (`en_subida: true`), añade su objetivo a `clausulazos_programados` para el momento exacto en que expire el escudo (o para la hora de inicio de la jornada si la apertura caía en la ventana de veto de 24h).\n"
        "   - NUNCA pagar cláusulas desproporcionadas ni de jugadores en caída de valor.\n"
        "10. GESTIÓN Y CANCELACIÓN DE ACCIONES PROGRAMADAS:\n"
        "   - Puedes ver el plan actual en `acciones_programadas_activas`.\n"
        "   - Si una puja programada anteriormente ya no es conveniente (ej. el jugador se lesionó, su valor empezó a desplomarse o ha surgido una oportunidad superior), puedes cancelarla en `cancelar_pujas_programadas`.\n"
        "=======================================================\n\n"
        "ESTRUCTURA DE RESPUESTA:\n"
        "1. Análisis Estratégico aplicando la Guía del Usuario (incluyendo análisis de presupuesto actual y proyectado).\n"
        "2. Decisiones de Mercado Libre, Clausulazos y Cancelaciones.\n"
        "3. Bloque JSON final estricto:\n"
        "```json\n"
        "{\n"
        '  "aplicar_alineacion": true,\n'
        '  "pujas_mercado_libre": [\n'
        '    {"marketId": 123888363, "nombre": "Dmitrovic", "puja_maxima": 987774}\n'
        "  ],\n"
        '  "clausulazos_inmediatos": [\n'
        '    {"playerId": 45678, "nombre": "Nombre Rival", "clausula": 3500000}\n'
        "  ],\n"
        '  "clausulazos_programados": [\n'
        '    {"playerId": 98765, "nombre": "Nombre Rival", "clausula": 4200000, "apertura_iso": "2026-09-02T15:30:00+02:00"}\n'
        "  ],\n"
        '  "cancelar_pujas_programadas": [\n'
        '    {"marketId": 12345, "nombre": "Jugador", "motivo": "Precio cayendo"}\n'
        "  ],\n"
        '  "aceptar_ofertas": [\n'
        '    {"marketId": 12345, "offerId": "xyz", "jugador": "Nombre", "cantidad": 6000000}\n'
        "  ],\n"
        '  "ventas_recomendadas": [],\n'
        '  "nueva_memoria": "Breve resumen actualizado de la situación y plan."\n'
        "}\n"
        "```"
    )

    user_prompt = (
        f"Memoria previa del mánager:\n{existing_memory}\n\n"
        f"Estado completo de la liga (fecha actual: {now_spain_str}):\n{json.dumps(situation, ensure_ascii=False, indent=2)}\n\n"
        "Aplica estrictamente la Guía Fantasy del Usuario, razona profundamente y decide qué compras de mercado libre, clausulazos u ofertas debemos gestionar."
    )

    print("· Consultando a Gemini 3.5 Flash Lite...")
    try:
        response = call_gemini(user_prompt, system_prompt, api_key, model=model)
        print("\n" + "=" * 60)
        print("🧠 DECISIÓN DEL MÁNAGER GEMINI:")
        print("=" * 60)
        print(response)
        print("=" * 60)

        events.emit("note", "🧠 Gemini Manager: Análisis estratégico y decisiones", detail={"analisis": response})

        # Parse JSON decision
        decision = {}
        if "```json" in response:
            json_str = response.split("```json")[1].split("```")[0].strip()
            decision = json.loads(json_str)

            # Update memory
            if decision.get("nueva_memoria"):
                os.makedirs(os.path.dirname(memory_path), exist_ok=True)
                with open(memory_path, "w", encoding="utf-8") as f:
                    f.write(f"# MEMORY\n\nÚltima actualización: {now_spain_str}\n\n{decision['nueva_memoria']}\n")
                print("\n[OK] Memoria persistente actualizada en hermes/MEMORY.md")

            # Save full multi-turn reasoning history permanently (Never overwrite)
            history_file = os.path.join(config.ROOT, ".state", "reasoning_history.json")
            os.makedirs(os.path.dirname(history_file), exist_ok=True)
            r_history = []
            if os.path.exists(history_file):
                try:
                    with open(history_file, "r", encoding="utf-8") as f:
                        r_history = json.load(f)
                except Exception:
                    r_history = []
            
            r_history.append({
                "timestamp": now_spain.isoformat(),
                "date_str": now_spain.strftime("%d/%m/%Y %H:%M"),
                "response": response,
                "decision": decision,
                "executed": execute
            })
            r_history = r_history[-50:]
            try:
                with open(history_file, "w", encoding="utf-8") as f:
                    json.dump(r_history, f, ensure_ascii=False, indent=2)
            except Exception:
                pass

            # Execute actions if requested
            if execute:
                print("\n⚡ MODO EJECUCIÓN ACTIVO: Aplicando decisiones en LaLiga Fantasy...")
                from . import bidding

                if decision.get("aplicar_alineacion") and best_lineup and not best_lineup.get("incomplete"):
                    try:
                        current_ids = []
                        execute_mod.apply_lineup(fc, tid, best_lineup, current_ids, dry_run=False)
                        print("  ✓ Alineación óptima aplicada en el juego.")
                    except Exception as e:
                        print(f"  ✗ No se pudo aplicar alineación: {e}")

                # 1. Execute Immediate Buyouts (Clausulazos directos)
                for c_item in decision.get("clausulazos_inmediatos", []):
                    raw_id = c_item.get("playerId")
                    clause_amt = c_item.get("clausula")
                    p_name = c_item.get("nombre") or f"Jugador #{raw_id}"
                    resolved_id = player_to_team_id.get(str(raw_id), raw_id)

                    # Safety check on trend
                    t_check = match_name(p_name, p_name, t_index) if t_index else None
                    if t_check and ((t_check.get("valor", 0) - t_check.get("valor1", 0)) < 0 or t_check.get("tendencia", 0) < 0):
                        diff_val = t_check.get("valor", 0) - t_check.get("valor1", 0)
                        print(f"  ⛔ Clausulazo INMEDIATO bloqueado por regla de valor ascendente: {p_name} está en bajada ({diff_val:+,} €/día)")
                        continue

                    if resolved_id and clause_amt:
                        try:
                            fc.pay_buyout_clause(lid, resolved_id, int(clause_amt))
                            print(f"  ⚡ ¡CLAUSULAZO PAGADO! Fichado {p_name} por {int(clause_amt):,} €")
                            events.emit("buyout", f"¡Clausulazo pagado! Fichado {p_name} ({int(clause_amt):,} €)")
                        except Exception as e:
                            print(f"  ✗ Error al pagar cláusula de {p_name}: {e}")

                # Register and validate scheduled buyout targets
                for sch_buyout in decision.get("clausulazos_programados", []):
                    sb_name = sch_buyout.get("nombre", "Desconocido")
                    sb_clause = sch_buyout.get("clausula", 0)
                    sb_iso = sch_buyout.get("apertura_iso")
                    t_check = match_name(sb_name, sb_name, t_index) if t_index else None
                    if t_check and ((t_check.get("valor", 0) - t_check.get("valor1", 0)) < 0 or t_check.get("tendencia", 0) < 0):
                        diff_val = t_check.get("valor", 0) - t_check.get("valor1", 0)
                        print(f"  ⛔ Clausulazo PROGRAMADO descartado por regla de valor ascendente: {sb_name} está en bajada ({diff_val:+,} €/día)")
                        continue
                    if sb_iso and sb_name:
                        state.add_reminder(f"buyout_{sch_buyout.get('playerId')}", f"Clausulazo programado sobre {sb_name} ({int(sb_clause):,} €)", sb_iso)

                # 2. Cancel requested scheduled bids
                for canc in decision.get("cancelar_pujas_programadas", []):
                    m_id = canc.get("marketId")
                    c_name = canc.get("nombre", str(m_id))
                    c_motivo = canc.get("motivo", "Decisión del mánager")
                    if m_id:
                        state.remove_bid_target(str(m_id))
                        print(f"  ✗ Puja programada CANCELADA: {c_name} (Motivo: {c_motivo})")
                        events.emit("cancel", f"Puja programada cancelada: {c_name}", detail={"motivo": c_motivo})

                # 3. Register free agent market bids in Last-Minute Sniping Bid Plan
                free_bids = decision.get("pujas_mercado_libre") or decision.get("pujas_recomendadas") or []
                for bid_item in free_bids:
                    m_id = bid_item.get("marketId")
                    max_bid = bid_item.get("puja_maxima")
                    nombre = bid_item.get("nombre", str(m_id))
                    if m_id and max_bid:
                        state.add_bid_target(str(m_id), int(max_bid), nombre=nombre)
                        print(f"  🎯 Objetivo añadido al Plan de Pujas de Último Minuto: {nombre} (Tope: {int(max_bid):,} €)")
                
                # 3. Execute last-minute bids using bidding engine
                try:
                    bidding.run_bid_plan(lid, dry_run=False)
                except Exception as e:
                    print(f"  ✗ Aviso en el motor de pujas de último minuto: {e}")

                # 4. Accept profitable offers on own players
                for accept_item in decision.get("aceptar_ofertas", []):
                    m_id = accept_item.get("marketId")
                    off_id = accept_item.get("offerId")
                    money = accept_item.get("cantidad")
                    ptid = accept_item.get("playerTeamId") or m_id
                    j_name = accept_item.get("jugador", str(m_id))
                    if (ptid or m_id) and off_id and money:
                        try:
                            fc.accept_offer(lid, ptid, off_id, int(money))
                            events.emit("sell", f"Oferta ACEPTADA por {j_name}: {int(money):,} €")
                            print(f"  ✓ Oferta ACEPTADA por {j_name}: {int(money):,} €")
                        except Exception as e:
                            print(f"  ✗ Error al aceptar oferta por {j_name}: {e}")

            # Generate Updated Minimalist Apple Dashboard
            from .dashboard_generator import generate_apple_dashboard
            generate_apple_dashboard(
                team=team,
                market=market,
                best_lineup=best_lineup,
                flips=flips,
                gaps=gaps,
                review_report=review_report,
                gemini_response=response,
                decision=decision,
                executed=execute,
                prob_index=prob_index,
                league_teams=league_teams,
                my_received_offers=my_received_offers,
                rival_clause_targets=rival_clause_targets
            )

    except Exception as e:
        print(f"[ERROR] Error al llamar a Gemini: {e}")


# Alias for backwards compatibility
run_gemini_manager = run_gemini_agent
