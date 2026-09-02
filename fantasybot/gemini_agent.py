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

    # Calculate buyout clause premium and days to amortize
    for r in rival_clause_targets:
        val = r.get("valor_mercado", 0)
        clause = r.get("clausula", 0)
        subida = r.get("variacion_diaria", 0)
        sobrecoste = max(0, clause - val)
        r["sobrecoste_clausula"] = sobrecoste
        r["dias_para_amortizar_sobrecoste"] = round(sobrecoste / subida, 1) if (sobrecoste > 0 and subida > 0) else (0 if sobrecoste == 0 else 999)

    # 6. Pre-calculate & Rank Top Clausulazo Opportunities & Gaps Solutions
    top_clausulazos_abiertos = sorted(
        [r for r in rival_clause_targets if r.get("clausula_abierta") and r.get("en_subida") and r.get("variacion_diaria", 0) > 0],
        key=lambda x: (x.get("ratio_clausula_valor", 99) <= 1.5, x.get("variacion_diaria", 0)),
        reverse=True
    )[:15]

    proximas_aperturas_escudos = sorted(
        [r for r in rival_clause_targets if not r.get("clausula_abierta") and r.get("en_subida") and r.get("segundos_para_abrir", 0) <= (72 * 3600)],
        key=lambda x: x.get("segundos_para_abrir", 0)
    )[:10]

    # Best candidate per missing gap
    candidatos_por_hueco = {}
    for pos_name in ["POR", "DEF", "MED", "DEL"]:
        rival_cands = [r for r in rival_clause_targets if r.get("posicion") == pos_name and r.get("clausula_abierta") and r.get("en_subida")]
        rival_cands = sorted(rival_cands, key=lambda x: x.get("variacion_diaria", 0), reverse=True)[:3]
        market_cands = [m for m in mercado_libre_sistema if m.get("posicion") == pos_name and m.get("en_subida")]
        market_cands = sorted(market_cands, key=lambda x: x.get("variacion_diaria", 0), reverse=True)[:3]
        candidatos_por_hueco[pos_name] = {
            "rivales_clausula_abierta": rival_cands,
            "mercado_libre": market_cands
        }

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
        "huecos_en_plantilla_sin_cubrir": gaps,
        "CANDIDATOS_PRIORITARIOS_PARA_CADA_HUECO": candidatos_por_hueco,
        "RANKING_TOP_CLAUSULAZOS_ABIERTOS_Y_SUBIENDO": top_clausulazos_abiertos,
        "PROXIMAS_APERTURAS_DE_ESCUDOS_EN_SUBIDA": proximas_aperturas_escudos,
        "mi_plantilla_actual": [
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
        "acciones_programadas_activas": scheduled_bids,
        "recordatorios_activos": state.load_reminders(),
        "alineacion_optima_calculada": best_lineup,
        "oportunidades_flip_especulacion": flips
    }

    system_prompt = (
        "Eres el Director Deportivo y Mánager General de Élite de un equipo en LALIGA Fantasy.\n"
        f"Fecha y hora actual: {now_spain_str}.\n"
        "Tu misión es maximizar el patrimonio económico y dominar la liga mediante decisiones financieras y tácticas implacables y profundamente razonadas.\n\n"
        "=== GUÍA FANTASY DEL USUARIO (FILOSOFÍA OBLIGATORIA) ===\n"
        "1. PRIORIDAD ABSOLUTA AL DINERO SOBRE LOS PUNTOS:\n"
        "   - A más dinero acumulado, mejores jugadores se fichan a medio/largo plazo y más puntos llegarán.\n"
        "2. PRIORIZAR JUGADORES CAROS EN SUBIDA:\n"
        "   - A mayor precio del jugador, mayores son sus subidas absolutas y más amplias son las oscilaciones de las ofertas del sistema (ej. un activo de 60M€ subiendo al 4% genera +2.4M€/día y ofertas del sistema con primas millonarias, mientras que uno de 2M€ apenas genera 50k€).\n"
        "3. VALOR ASCENDENTE (REGLA DE ORO):\n"
        "   - Priorizar tener siempre a toda la plantilla con valor de mercado en subida (`en_subida: true`).\n"
        "   - PROHIBICIÓN TOTAL: NUNCA pujar ni clausular a ningún jugador con `en_subida: false` o tendencia `BAJANDO` (ej. jugadores en caída libre). Comprar activos depreciándose destruye el patrimonio del club.\n"
        "4. JUGADORES SIEMPRE EN VENTA & MONETIZACIÓN EN EL PICO:\n"
        "   - Toda la plantilla debe estar siempre listada en el mercado para recibir ofertas diarias de la máquina.\n"
        "   - Vender activos cuando alcancen su pico de valor o cuando su ritmo de subida empiece a frenar, monetizando plusvalías máximas.\n"
        "   - Cuidado con piezas imprescindibles: NO vender titulares clave sin tener un recambio garantizado en subida.\n"
        "5. PROTECCIÓN DE CLÁUSULAS: VENDER ANTES QUE SUBIR CLÁUSULAS:\n"
        "   - Subir la cláusula de un jugador propio entierra dinero muerto que no genera rentabilidad.\n"
        "   - Si el escudo de 14 días de un jugador cotizado va a expirar y los rivales tienen dinero para robártelo, la jugada maestra es ACEPTAR UNA BUENA OFERTA de la máquina e ingresar decenas de millones limpios para reinvertir en activos que suban más rápido.\n"
        "6. MERCADO LIBRE Y PUJAS DISCIPLINADAS:\n"
        "   - Solo pujar por jugadores en subida clara o con rendimiento sobresaliente.\n"
        "   - Si un jugador NO tiene pujas rivales: pujar a su PRECIO OFICIAL o sumar exactamente +210 € como margen de seguridad.\n"
        "   - Si ya tiene competencia: subir ligeramente para asegurar la compra, pero NUNCA sobrepagar por encima de su valor real.\n"
        "   - Riesgo de corrección: si un jugador ya ha subido demasiado, no comprar por inercia; evaluar si está cerca de su techo.\n"
        "7. CLAUSULAZOS RIVALES (RENTABILIDAD A FUTURO, NO SÓLO 1.0x):\n"
        "   - NUNCA hacer ofertas directas a rivales (solo mercado libre o pago de cláusulas).\n"
        "   - Check rápido de 24h previas a la jornada: Si `0 < horas_para_inicio_jornada <= 24`, LaLiga bloquea el pago directo de cláusulas; en ese caso programar para la reapertura (al arrancar la jornada se vuelven a desbloquear). Si faltan >24h o la jornada ya arrancó, LUZ VERDE TOTAL.\n"
        "   - CRITERIO DE RENTABILIDAD A FUTURO: Los clausulazos NO tienen que ser exclusivamente a ratio 1.0x. Evalúa la ecuación `(Cláusula - Valor) vs Subida Diaria`. Si la subida diaria amortiza el sobrecoste en pocos días (ratios 1.1x a 1.5x) y el jugador tiene recorrido alcista claro o supone un salto de calidad indiscutible, la operación es altamente rentable y se aprueba.\n"
        "   - 'Flipping de Clausulazos': Si un rival tiene un activo caro en plena subida con cláusula asumible, se puede clausular para ponerlo en el mercado ese mismo día y capturar una oferta millonaria de la máquina en 24-48 horas.\n"
        "8. ALINEACIÓN COMPETITIVA:\n"
        "   - Cruzar probabilidad de titularidad con la dificultad del rival de LaLiga (partidos duros restan puntuación esperada; partidos asequibles la aumentan).\n"
        "9. CONTROL RIGUROSO DE CAJA:\n"
        "   - Mantener siempre saldo positivo tras las operaciones y preservar liquidez proyectada para imprevistos y pujas.\n"
        "=======================================================\n\n"
        "PROTOCOLO DE RAZONAMIENTO OBLIGATORIO (ANÁLISIS PROFUNDO POR FASES):\n\n"
        "### FASE 1: AUDITORÍA DE PLANTILLA PROPIA Y OFERTAS ENTRANTES (TRADING & MONETIZACIÓN)\n"
        "- Evaluar ofertas recibidas del sistema o rivales: calcular el beneficio neto y decidir si se aceptan para capturar picos de valor.\n"
        "- Revisar activos propios caros: estado de subida/bajada diaria, amortización y posibles riesgos de devaluación.\n"
        "- Evaluar escudos propios por vencer: ante riesgo de robo rival, planificar venta lucrativa antes que subir cláusulas.\n\n"
        "### FASE 2: RADAR DE MERCADO LIBRE Y SNIPING\n"
        "- Analizar futbolistas libres del sistema, priorizando activos de alto valor en plena subida diaria.\n"
        "- Justificar pujas: aplicar la regla de +210 € si está solo, o margen competitivo moderado sin sobrepagar si hay rivales pujando.\n\n"
        "### FASE 3: RADAR DE CLAUSULAZOS Y OFENSIVA A RIVALES\n"
        "- Comprobación rápida de ventana de 24h antes del primer partido.\n"
        "- Auditoría exhaustiva de objetivos: evaluar ratio de cláusula vs valor, subida diaria (+€/día) y días necesarios para amortizar la prima. Dictaminar con argumentos matemáticos qué compras son rentables a futuro (para el once o para flipping) y cuáles se descartan.\n\n"
        "### FASE 4: BALANCE MATEMÁTICO DE TESORERÍA Y CAJA\n"
        "- Desglosar números: Presupuesto inicial en caja, dinero ingresado por ventas, coste total de clausulazos y pujas decididas, y saldo restante de seguridad asegurado.\n\n"
        "### BLOQUE JSON FINAL ESTRICTO:\n"
        "```json\n"
        "{\n"
        '  "aplicar_alineacion": true,\n'
        '  "pujas_mercado_libre": [\n'
        '    {"marketId": 123888363, "nombre": "Nombre Mercado", "puja_maxima": 987774}\n'
        "  ],\n"
        '  "clausulazos_inmediatos": [\n'
        '    {"playerId": 45678, "nombre": "Nombre Rival", "clausula": 3500000}\n'
        "  ],\n"
        '  "clausulazos_programados": [\n'
        '    {"playerId": 98765, "nombre": "Nombre Rival", "clausula": 4200000, "apertura_iso": "2026-09-03T22:18:00+02:00"}\n'
        "  ],\n"
        '  "cancelar_pujas_programadas": [],\n'
        '  "aceptar_ofertas": [],\n'
        '  "ventas_recomendadas": [],\n'
        '  "nueva_memoria": "Resumen ejecutivo profundo de la situación, balance y plan estratégico."\n'
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

                # Refresh team, market, and league_teams after real operations
                try:
                    team = fc.team(lid, tid)
                    market = fc.market(lid)
                    league_teams = fc.league_teams(lid)
                    # Auto list newly bought players on the market
                    for p in team.get("players", []):
                        pm = p.get("playerMaster", {})
                        p_name = pm.get("nickname") or pm.get("name")
                        ptid = p.get("playerTeamId")
                        m_val = pm.get("marketValue") or 0
                        is_on_market = bool(p.get("playerMarket"))
                        if ptid and not is_on_market and m_val > 0:
                            try:
                                fc.sell_player(lid, ptid, int(m_val))
                                print(f"  ✓ Nuevo fichaje puesto en venta: {p_name} ({int(m_val):,} €)")
                            except Exception:
                                pass
                    team = fc.team(lid, tid)
                    market = fc.market(lid)
                except Exception as e:
                    print(f"  ✗ Aviso al refrescar estado del equipo: {e}")

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
