"""Agentic Gemini Manager for FantasyBot.

Implements an autonomous, interactive ReAct / Function Calling loop (up to 7 steps)
where Gemini dynamically queries finances, accepts sales, verifies updated cash,
executes buyouts, optimizes tactical formations, and produces verified reports.
"""

import sys
import json
import os
import time
import urllib.request
import urllib.error
from datetime import datetime, timezone, timedelta

# Fix Windows console UTF-8 emoji encoding
if hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

from . import config, events, execute as execute_mod, state
from .strategy import flip, lineup as lineup_opt, needs as needs_mod
from .api import FantasyClient
from .sources.lineups import probable_lineups
from .sources.market_trends import trends_index
from .dashboard_generator import generate_apple_dashboard
from .matching import match_name

try:
    from zoneinfo import ZoneInfo
    SPAIN_TZ = ZoneInfo("Europe/Madrid")
except Exception:
    SPAIN_TZ = timezone(timedelta(hours=2))

MAX_AGENT_STEPS = 7


def _get_candidate_models(model: str = "gemini-flash-lite-latest") -> list:
    return [model, "gemini-flash-lite-latest", "gemini-flash-latest"]


def _call_gemini_turn(contents: list, system_instruction: str, tools_decl: list, api_key: str, model: str = "gemini-flash-lite-latest") -> dict:
    """Sends a single turn in the multi-turn function calling conversation to Gemini."""
    candidate_models = _get_candidate_models(model)
    last_err = None

    payload = {
        "contents": contents,
        "systemInstruction": {
            "parts": [{"text": system_instruction}]
        },
        "tools": tools_decl,
        "generationConfig": {
            "temperature": 0.2,
            "maxOutputTokens": 4096
        }
    }

    data_bytes = json.dumps(payload, ensure_ascii=False).encode("utf-8")

    for m in candidate_models:
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{m}:generateContent?key={api_key}"
        req = urllib.request.Request(
            url,
            data=data_bytes,
            headers={"Content-Type": "application/json; charset=utf-8"}
        )
        try:
            with urllib.request.urlopen(req, timeout=60) as resp:
                result = json.loads(resp.read().decode("utf-8"))
            candidates = result.get("candidates", [])
            if candidates:
                return candidates[0].get("content", {})
        except Exception as e:
            last_err = e
            continue

    raise last_err or ValueError("Failed to call Gemini API in agentic turn.")


def run_agentic_manager(execute: bool = False, model: str = "gemini-flash-lite-latest") -> dict:
    """Runs the autonomous, interactive Agentic Manager for FantasyBot."""
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
        return {}

    now_spain = datetime.now(SPAIN_TZ)
    now_utc = datetime.now(timezone.utc)
    now_spain_str = now_spain.strftime("%A, %d de %B de %Y a las %H:%M:%S (Hora España)")

    print("=" * 60)
    print("🤖 INICIANDO MODO AGÉNTICO FANTASYBOT (GEMINI FUNCTION CALLING)")
    print(f"🕒 Fecha y Hora: {now_spain_str}")
    print(f"⚡ Modo de Ejecución: {'REAL (LaLiga Fantasy)' if execute else 'SIMULACIÓN (Dry-Run)'}")
    print(f"🛑 Límite de seguridad: Máximo {MAX_AGENT_STEPS} pasos por sesión")
    print("=" * 60)

    fc = FantasyClient()
    lid, tid = fc.default_ids()

    print("· Sincronizando estado inicial con LaLiga Fantasy...")
    team = fc.team(lid, tid)
    market = fc.market(lid)
    league_teams = fc.league_teams(lid)

    # 1. Deterministic Squad Auto-Listing (Poner siempre a toda la plantilla en venta)
    for p in team.get("players", []):
        pm = p.get("playerMaster", {})
        pt_id = p.get("playerTeamId")
        p_name = pm.get("nickname") or pm.get("name") or "Desconocido"
        m_val = pm.get("marketValue") or 0
        already_listed = p.get("playerMarket") is not None
        if pt_id and not already_listed and m_val > 0:
            if execute:
                try:
                    fc.sell_player(lid, pt_id, int(m_val))
                    print(f"  ✓ Auto-listado en venta: {p_name} por {int(m_val):,} €")
                    events.emit("sell", f"Puesto a la venta: {p_name} ({int(m_val):,} €)")
                except Exception as e:
                    print(f"  · Info al listar {p_name}: {e}")
            else:
                print(f"  [Simulación] Se auto-listaría en venta: {p_name} ({int(m_val):,} €)")

    # 2. Context helpers
    try:
        t_index = trends_index()
    except Exception:
        t_index = None

    try:
        prob_index = probable_lineups()
    except Exception:
        prob_index = None

    # Calculate hours to next kickoff
    hours_to_kickoff = None
    veto_24h_activo = False
    try:
        from .sources import matchday
        next_ko = matchday.next_kickoff(fc)
        if next_ko:
            ko_dt = datetime.fromisoformat(next_ko)
            if ko_dt.tzinfo is None:
                ko_dt = ko_dt.replace(tzinfo=timezone.utc)
            diff_secs = (ko_dt - now_utc).total_seconds()
            hours_to_kickoff = round(diff_secs / 3600.0, 1)
            if 0 < hours_to_kickoff <= 24:
                veto_24h_activo = True
    except Exception:
        pass

    # Build player to team id mapping for rival buyouts
    player_to_team_id = {}
    for lt in league_teams:
        for p in lt.get("players", []):
            pm = p.get("playerMaster", {})
            ptid = p.get("playerTeamId")
            if ptid:
                player_to_team_id[str(ptid)] = ptid
                if pm.get("id"):
                    player_to_team_id[str(pm.get("id"))] = ptid

    simulated_money = team.get("teamMoney", 0)
    executed_actions_log = []

    # -------------------------------------------------------------
    # Tool Declarations for Gemini
    # -------------------------------------------------------------
    tools_declaration = [
        {
            "function_declarations": [
                {
                    "name": "consultar_caja_y_plantilla",
                    "description": "Consulta el saldo en efectivo disponible en caja, valor de plantilla, patrimonio total, huecos en el 11 titular y horas para la jornada.",
                    "parameters": {
                        "type": "OBJECT",
                        "properties": {}
                    }
                },
                {
                    "name": "evaluar_ofertas_recibidas",
                    "description": "Obtiene la lista completa de ofertas pendientes recibidas por nuestros jugadores (del sistema o rivales), con porcentaje de diferencia sobre valor de mercado y tendencia diaria (SUBIENDO o BAJANDO).",
                    "parameters": {
                        "type": "OBJECT",
                        "properties": {}
                    }
                },
                {
                    "name": "aceptar_oferta_mercado",
                    "description": "Acepta la oferta de la máquina o rival por un jugador propio. Prioridad total a ofertas que estén por encima de su valor de mercado (+0% a +5%). Si un jugador lleva bajando con fuerza y hay que liquidarlo en 2-3 días, se puede aceptar con descuento leve, pero nunca si la oferta es un robo abusivo (descuento excesivo inferior a -3%).",
                    "parameters": {
                        "type": "OBJECT",
                        "properties": {
                            "offer_id": {"type": "STRING", "description": "ID de la oferta a aceptar"},
                            "jugador": {"type": "STRING", "description": "Nombre del jugador"}
                        },
                        "required": ["offer_id"]
                    }
                },
                {
                    "name": "buscar_clausulazos_viables",
                    "description": "Busca auténticas gangas y oportunidades en equipos rivales: jugadores en subida de valor diaria, con escudo abierto, ratio cláusula/valor bajo (máx 1.10x) y amortización del sobrecoste en menos de 3-4 días. Se pueden realizar todos los clausulazos que se quieran siempre que sean gangas rentables.",
                    "parameters": {
                        "type": "OBJECT",
                        "properties": {
                            "max_precio": {"type": "INTEGER", "description": "Precio máximo de cláusula a buscar en euros"},
                            "posicion": {"type": "STRING", "description": "Filtro opcional de posición: POR, DEF, MED o DEL"}
                        }
                    }
                },
                {
                    "name": "buscar_mercado_libre",
                    "description": "Busca jugadores libres que están hoy en el mercado del sistema, con su precio de salida y ritmo de subida diaria.",
                    "parameters": {
                        "type": "OBJECT",
                        "properties": {
                            "max_precio": {"type": "INTEGER", "description": "Precio máximo a buscar"},
                            "min_subida_diaria": {"type": "INTEGER", "description": "Mínima subida diaria en euros requerida"}
                        }
                    }
                },
                {
                    "name": "ejecutar_clausulazo",
                    "description": "Paga la cláusula de rescisión de una ganga rival en LaLiga Fantasy. Solo permitido si el ratio cláusula/valor es razonable (máx 1.15x) y se amortiza rápidamente.",
                    "parameters": {
                        "type": "OBJECT",
                        "properties": {
                            "player_id": {"type": "STRING", "description": "ID del jugador a clausular"},
                            "nombre": {"type": "STRING", "description": "Nombre del jugador"},
                            "precio_clausula": {"type": "INTEGER", "description": "Importe exacto de la cláusula"}
                        },
                        "required": ["player_id", "precio_clausula"]
                    }
                },
                {
                    "name": "programar_puja_mercado",
                    "description": "Registra una puja por un jugador de mercado libre para el plan de sniping de último minuto (cierre a las 22:18).",
                    "parameters": {
                        "type": "OBJECT",
                        "properties": {
                            "market_id": {"type": "STRING", "description": "ID del mercado del jugador"},
                            "nombre": {"type": "STRING", "description": "Nombre del jugador"},
                            "precio_maximo": {"type": "INTEGER", "description": "Tope máximo a pujar"}
                        },
                        "required": ["market_id", "precio_maximo"]
                    }
                },
                {
                    "name": "alinear_equipo",
                    "description": "Calcula y aplica la alineación táctica óptima entre todas las formaciones posibles (3-4-3, 3-5-2, 4-3-3, 4-4-2, 4-5-1, 5-3-2, 5-4-1) según los futbolistas disponibles en plantilla.",
                    "parameters": {
                        "type": "OBJECT",
                        "properties": {}
                    }
                },
                {
                    "name": "finalizar_sesion",
                    "description": "Concluye el turno del mánager emitiendo el resumen táctico definitivo de la jornada, la actualización de tesorería y la nueva memoria a largo plazo.",
                    "parameters": {
                        "type": "OBJECT",
                        "properties": {
                            "resumen_tactico": {"type": "STRING", "description": "Informe completo y estructurado de todas las operaciones y decisiones tomadas"},
                            "nueva_memoria": {"type": "STRING", "description": "Resumen conciso y estratégico para persistir en hermes/MEMORY.md"}
                        },
                        "required": ["resumen_tactico", "nueva_memoria"]
                    }
                }
            ]
        }
    ]

    # -------------------------------------------------------------
    # Tool Execution Handlers
    # -------------------------------------------------------------
    def handle_consultar_caja() -> dict:
        nonlocal team, simulated_money
        if execute:
            try:
                team = fc.team(lid, tid)
            except Exception:
                pass
            caja = team.get("teamMoney", 0)
        else:
            caja = simulated_money

        val = team.get("teamValue", 0)
        squad_summary = {}
        for p in team.get("players", []):
            pm = p.get("playerMaster", {})
            pos = pm.get("positionId")
            pos_label = {1: "POR", 2: "DEF", 3: "MED", 4: "DEL"}.get(pos, "OTRO")
            p_name = pm.get("nickname") or pm.get("name")
            squad_summary.setdefault(pos_label, []).append(f"{p_name} ({int(pm.get('marketValue', 0)):,} €)")

        return {
            "saldo_en_caja": caja,
            "saldo_formateado": f"{int(caja):,} €",
            "valor_plantilla": val,
            "valor_plantilla_formateado": f"{int(val):,} €",
            "patrimonio_total": caja + val,
            "patrimonio_total_formateado": f"{int(caja + val):,} €",
            "total_jugadores": len(team.get("players", [])),
            "desglose_plantilla": squad_summary,
            "horas_para_inicio_jornada": hours_to_kickoff,
            "veto_clausulazos_24h_activo": veto_24h_activo,
            "clausulazos_inmediatos_permitidos": not veto_24h_activo
        }

    def handle_evaluar_ofertas() -> list:
        offers_list = []
        for p in team.get("players", []):
            pm = p.get("playerMaster", {})
            ptid = p.get("playerTeamId")
            pname = pm.get("nickname") or pm.get("name")
            val = pm.get("marketValue") or 0
            mid = p.get("playerMarket", {}).get("id")
            pos = pm.get("positionId")
            pos_label = {1: "POR", 2: "DEF", 3: "MED", 4: "DEL"}.get(pos, "JUG")
            if ptid and p.get("playerMarket"):
                try:
                    offs = fc.player_offers(lid, ptid)
                    if isinstance(offs, list):
                        for off in offs:
                            if off.get("status") == "pending":
                                off_id = off.get("id")
                                amt = off.get("money") or 0
                                diff_pct = round(((amt - val) / val) * 100, 2) if val else 0
                                buyer = "Sistema (Mercado)" if off.get("isFromMarket") else "Rival"
                                trend = "ESTABLE"
                                diff_val = 0
                                if t_index:
                                    tc = match_name(pname, pname, t_index)
                                    if tc:
                                        diff_val = tc.get("valor", 0) - tc.get("valor1", 0)
                                        trend = "SUBIENDO" if diff_val > 0 else "BAJANDO" if diff_val < 0 else "ESTABLE"
                                offers_list.append({
                                    "offerId": str(off_id),
                                    "jugador": pname,
                                    "posicion": pos_label,
                                    "playerTeamId": ptid,
                                    "marketId": mid,
                                    "oferta_recibida": amt,
                                    "oferta_formateada": f"{int(amt):,} €",
                                    "valor_mercado": val,
                                    "diferencia_pct": f"{diff_pct:+}%",
                                    "subida_diaria": f"{int(diff_val):+,} €/día",
                                    "tendencia": trend,
                                    "comprador": buyer
                                })
                except Exception:
                    pass
        return offers_list

    def handle_aceptar_oferta(args: dict) -> dict:
        nonlocal team, simulated_money
        off_id = str(args.get("offer_id", "")).strip()
        j_name = args.get("jugador", "")
        all_offs = handle_evaluar_ofertas()
        matched = None
        for o in all_offs:
            if o["offerId"] == off_id or (j_name and o["jugador"].lower() == j_name.lower()):
                matched = o
                break

        if not matched:
            return {"status": "error", "mensaje": f"No se encontró ninguna oferta pendiente con ID {off_id}"}

        amt = matched["oferta_recibida"]
        ptid = matched["playerTeamId"]
        mid = matched.get("marketId")
        pname = matched["jugador"]
        val = matched.get("valor_mercado", 0)
        diff_val = 0
        if t_index:
            tc = match_name(pname, pname, t_index)
            if tc:
                diff_val = tc.get("valor", 0) - tc.get("valor1", 0)

        diff_pct = ((amt - val) / val) * 100.0 if val else 0.0

        # FILTROS DE SEGURIDAD EN VENTAS:
        # 1. Si el jugador está subiendo con fuerza (> 50k€/día), PROHIBIDO vender
        if diff_val > 50000:
            return {
                "status": "denegado",
                "mensaje": f"Venta denegada por seguridad: {pname} está en plena subida alcista ({diff_val:+,} €/día). No se debe vender a un jugador que se revaloriza cada día."
            }

        # 2. Si la oferta es un robo abusivo (descuento excesivo inferior a -3.0% sobre su valor)
        # Solo se permite un descuento leve (entre 0% y -3%) si el jugador está bajando con fuerza para liquidarlo en 2-3 días.
        if diff_pct < -3.0:
            return {
                "status": "denegado",
                "mensaje": f"Venta denegada: La oferta por {pname} ({int(amt):,} €) tiene un descuento abusivo del {diff_pct:.2f}% (por debajo del -3% permitido). Es mejor esperar a mañana a una oferta superior del mercado."
            }

        target_endpoint_id = mid or ptid

        if execute:
            try:
                fc.accept_offer(lid, target_endpoint_id, matched["offerId"], int(amt))
                events.emit("sell", f"Oferta ACEPTADA por {pname}: {int(amt):,} €")
                print(f"  ⚡ [EJECUCIÓN REAL] Oferta ACEPTADA por {pname}: {int(amt):,} €")
                team = fc.team(lid, tid)
                simulated_money = team.get("teamMoney", 0)
                executed_actions_log.append(f"Venta de {pname} por {int(amt):,} €")
                return {
                    "status": "exito",
                    "mensaje": f"Oferta aceptada en LaLiga Fantasy. Vendido {pname} por {int(amt):,} €.",
                    "nuevo_saldo_en_caja": simulated_money,
                    "nuevo_saldo_formateado": f"{int(simulated_money):,} €"
                }
            except Exception as e:
                return {"status": "error", "mensaje": f"Error al llamar a la API de LaLiga: {e}"}
        else:
            simulated_money += amt
            executed_actions_log.append(f"Venta de {pname} por {int(amt):,} € (Simulada)")
            print(f"  [SIMULACIÓN] Oferta aceptada por {pname}: {int(amt):,} € (Saldo proyectado: {int(simulated_money):,} €)")
            return {
                "status": "simulacion_exitosa",
                "mensaje": f"[Simulación] Se aceptaría oferta por {pname} ingresando {int(amt):,} €.",
                "nuevo_saldo_en_caja": simulated_money,
                "nuevo_saldo_formateado": f"{int(simulated_money):,} €"
            }

    def handle_buscar_clausulazos(args: dict) -> list:
        nonlocal simulated_money
        caja = team.get("teamMoney", 0) if execute else simulated_money
        max_p = args.get("max_precio") or caja
        pos_filter = args.get("posicion")

        candidates = []
        pos_map = {1: "POR", 2: "DEF", 3: "MED", 4: "DEL"}

        for lt in league_teams:
            if str(lt.get("id")) == str(tid):
                continue
            m_name = lt.get("manager", {}).get("managerName") or lt.get("teamName") or "Rival"
            for p in lt.get("players", []):
                pm = p.get("playerMaster", {})
                pid = pm.get("id")
                name = pm.get("nickname") or pm.get("name")
                pos_id = pm.get("positionId")
                pos_str = pos_map.get(pos_id, "JUG")
                if pos_filter and pos_str != pos_filter.upper():
                    continue
                val = pm.get("marketValue") or 0
                clause = p.get("buyoutClause") or p.get("playerTeam", {}).get("buyoutClause") or val
                locked_until = p.get("buyoutClauseLockedEndTime")

                is_open = True
                if locked_until:
                    try:
                        exp_dt = datetime.fromisoformat(locked_until)
                        if exp_dt.tzinfo is None:
                            exp_dt = exp_dt.replace(tzinfo=timezone.utc)
                        if (exp_dt - now_utc).total_seconds() > 0:
                            is_open = False
                    except Exception:
                        pass

                diff_val = 0
                if t_index:
                    tc = match_name(name, name, t_index)
                    if tc:
                        diff_val = tc.get("valor", 0) - tc.get("valor1", 0)

                if clause <= max_p and is_open and diff_val > 0:
                    ratio = round(clause / val, 2) if val else 1.0
                    overcost = max(0, clause - val)
                    amort_days = round(overcost / diff_val, 1) if diff_val > 0 else 999

                    # Filtro de Sanidad Financiera: Solo gangas reales
                    # Si tiene sobrecoste, el ratio no puede superar 1.10x y debe amortizarse en menos de 4 días
                    if ratio <= 1.10 and amort_days <= 4.0:
                        candidates.append({
                            "playerId": str(pid),
                            "nombre": name,
                            "posicion": pos_str,
                            "equipo_rival": m_name,
                            "clausula": int(clause),
                            "clausula_formateada": f"{int(clause):,} €",
                            "valor_mercado": f"{int(val):,} €",
                            "subida_diaria": f"{int(diff_val):+,} €/día",
                            "dias_para_amortizar_sobrecoste": amort_days,
                            "ratio_clausula_valor": ratio
                        })

        candidates.sort(key=lambda x: (x["ratio_clausula_valor"], x["dias_para_amortizar_sobrecoste"], -int(x["clausula"])))
        return candidates[:8]

    def handle_buscar_mercado_libre(args: dict) -> list:
        nonlocal simulated_money
        caja = team.get("teamMoney", 0) if execute else simulated_money
        max_p = args.get("max_precio") or caja
        min_subida = args.get("min_subida_diaria") or 0

        free_agents = []
        pos_map = {1: "POR", 2: "DEF", 3: "MED", 4: "DEL"}

        for m in market:
            if m.get("discr") != "marketPlayerLeague":
                continue
            pm = m.get("playerMaster", {})
            name = pm.get("nickname") or pm.get("name")
            pos_str = pos_map.get(pm.get("positionId"), "JUG")
            price = m.get("salePrice") or pm.get("marketValue") or 0

            diff_val = 0
            if t_index:
                tc = match_name(name, name, t_index)
                if tc:
                    diff_val = tc.get("valor", 0) - tc.get("valor1", 0)

            if price <= max_p and diff_val >= min_subida:
                free_agents.append({
                    "marketId": str(m.get("id")),
                    "nombre": name,
                    "posicion": pos_str,
                    "precio_salida": int(price),
                    "precio_formateado": f"{int(price):,} €",
                    "subida_diaria": f"{int(diff_val):+,} €/día",
                    "cierre": m.get("expirationDate")
                })

        free_agents.sort(key=lambda x: -x["precio_salida"])
        return free_agents[:8]

    def handle_ejecutar_clausulazo(args: dict) -> dict:
        nonlocal team, simulated_money
        caja = team.get("teamMoney", 0) if execute else simulated_money
        pid = str(args.get("player_id", "")).strip()
        name = args.get("nombre", f"Jugador #{pid}")
        cost = int(args.get("precio_clausula", 0))

        if veto_24h_activo:
            return {"status": "bloqueado", "mensaje": f"Quedan menos de 24h para el partido ({hours_to_kickoff}h). LaLiga bloquea clausulazos hasta el inicio de la jornada."}

        if cost > caja:
            return {"status": "error_saldo", "mensaje": f"Saldo insuficiente. Cuesta {cost:,} € y dispones de {caja:,} €."}

        # Guardrail estricto contra clausulazos inflados:
        target_val = 0
        target_diff = 0
        for lt in league_teams:
            for p in lt.get("players", []):
                pm = p.get("playerMaster", {})
                if str(pm.get("id")) == pid or (pm.get("nickname") or pm.get("name")) == name:
                    target_val = pm.get("marketValue") or 0
                    if t_index:
                        tc = match_name(name, name, t_index)
                        if tc:
                            target_diff = tc.get("valor", 0) - tc.get("valor1", 0)
                    break

        if target_val > 0:
            ratio = cost / target_val
            if ratio > 1.10:
                return {
                    "status": "denegado",
                    "mensaje": f"Operación denegada por seguridad: La cláusula de {name} ({cost:,} €) supera en más de un 10% su valor de mercado ({target_val:,} €, ratio {ratio:.2f}x). Solo se permiten gangas rentables (máximo 1.10x)."
                }
            overcost = max(0, cost - target_val)
            if target_diff <= 0 and overcost > 0:
                return {
                    "status": "denegado",
                    "mensaje": f"Operación denegada: {name} no está subiendo de valor diariamente para amortizar el sobrecoste."
                }
            if target_diff > 0 and overcost > 0:
                days_to_amort = overcost / target_diff
                if days_to_amort > 4.0:
                    return {
                        "status": "denegado",
                        "mensaje": f"Operación denegada: El sobrecoste de {name} tardaría {days_to_amort:.1f} días en amortizarse (límite máximo permitido: 4 días)."
                    }

        resolved_ptid = player_to_team_id.get(pid, pid)

        if execute:
            try:
                fc.pay_buyout_clause(lid, resolved_ptid, cost)
                events.emit("buyout", f"¡Clausulazo pagado! Fichado {name} ({cost:,} €)")
                print(f"  ⚡ [EJECUCIÓN REAL] ¡CLAUSULAZO PAGADO! {name} por {cost:,} €")

                team = fc.team(lid, tid)
                simulated_money = team.get("teamMoney", 0)

                for p in team.get("players", []):
                    pm = p.get("playerMaster", {})
                    if str(pm.get("id")) == pid or (pm.get("nickname") or pm.get("name")) == name:
                        pt_id = p.get("playerTeamId")
                        m_val = pm.get("marketValue") or 0
                        if pt_id and m_val > 0:
                            try:
                                fc.sell_player(lid, pt_id, int(m_val))
                                print(f"  ✓ Nuevo fichaje puesto en venta: {name} ({int(m_val):,} €)")
                            except Exception:
                                pass

                executed_actions_log.append(f"Clausulazo a {name} por {cost:,} €")
                return {
                    "status": "exito",
                    "mensaje": f"Clausulazo completado en LaLiga. {name} fichado y puesto en venta.",
                    "saldo_restante_en_caja": simulated_money,
                    "saldo_restante_formateado": f"{int(simulated_money):,} €"
                }
            except Exception as e:
                return {"status": "error", "mensaje": f"Error al llamar a la API de LaLiga: {e}"}
        else:
            simulated_money -= cost
            executed_actions_log.append(f"Clausulazo a {name} por {cost:,} € (Simulado)")
            print(f"  [SIMULACIÓN] Clausulazo ejecutado sobre {name}: {cost:,} € (Saldo proyectado: {int(simulated_money):,} €)")
            return {
                "status": "simulacion_exitosa",
                "mensaje": f"[Simulación] Se compraría por cláusula a {name} por {cost:,} € y se auto-listaría en venta.",
                "saldo_restante_en_caja": simulated_money,
                "saldo_restante_formateado": f"{int(simulated_money):,} €"
            }

    def handle_programar_puja(args: dict) -> dict:
        mid = str(args.get("market_id", "")).strip()
        name = args.get("nombre", f"Mercado #{mid}")
        max_b = int(args.get("precio_maximo", 0))

        state.add_bid_target(mid, max_b, nombre=name)
        executed_actions_log.append(f"Puja programada para las 22:18 por {name} (Tope: {max_b:,} €)")
        print(f"  🎯 Objetivo de puja registrado para las 22:18: {name} (Tope: {max_b:,} €)")
        return {
            "status": "exito",
            "mensaje": f"Puja registrada en el plan de último minuto para {name} con tope de {max_b:,} €."
        }

    def handle_alinear_equipo() -> dict:
        try:
            best = lineup_opt.optimize(team, prob_index, t_index)
            if best and not best.get("incomplete"):
                form_shape = "-".join(str(x) for x in best.get("formation", (3, 4, 3)))
                starters = [p.get("nickname") or p.get("name") for p in best.get("starters", [])]
                if execute:
                    current_ids = []
                    execute_mod.apply_lineup(fc, tid, best, current_ids, dry_run=False)
                    executed_actions_log.append(f"Alineación táctica {form_shape} aplicada")
                    print(f"  ✓ [EJECUCIÓN REAL] Alineación {form_shape} aplicada en LaLiga Fantasy.")
                else:
                    executed_actions_log.append(f"Alineación táctica {form_shape} calculada (Simulada)")
                    print(f"  [SIMULACIÓN] Alineación óptima calculada: {form_shape}")

                return {
                    "status": "exito",
                    "formacion_elegida": form_shape,
                    "once_titular": starters,
                    "puntos_esperados": round(best.get("total_score", 0), 1),
                    "capitan": (best.get("captain") or {}).get("nickname") or (best.get("captain") or {}).get("name")
                }
        except Exception as e:
            return {"status": "error", "mensaje": f"Error al calcular alineación: {e}"}

        return {"status": "incompleta", "mensaje": "Plantilla sin efectivos suficientes para un 11 legal completo."}

    # System instruction
    system_instruction = (
        "Eres el Mánager Deportivo y Broker Financiero de Élite de FantasyBot para LaLiga Fantasy.\n"
        "Operas en MODO AGÉNTICO INTERACTIVO mediante herramientas (Function Calling).\n"
        "TU OBJETIVO ES MAXIMIZAR EL PATRIMONIO DEL CLUB Y COMPETIR CON SOLVENCIA.\n\n"
        "FILOSOFÍA OBLIGATORIA DEL USUARIO:\n"
        "1. PRIORIDAD ABSOLUTA AL DINERO SOBRE LOS PUNTOS: A más dinero, mejores jugadores y más puntos.\n"
        "2. PROTEGER ACTIVOS ALCISTAS: NUNCA vender a futbolistas que estén en aceleración alcista subiendo con fuerza cada día.\n"
        "3. CRITERIO INTELIGENTE DE VENTA: Aceptar siempre ofertas por encima del valor de mercado (+0% a +5%). Si un jugador está bajando fuerte de valor, hay que liquidarlo en 2-3 días; se puede aceptar una oferta con descuento leve si es necesario, pero NUNCA aceptar ofertas que sean un robo abusivo (descuento excesivo inferior a -3%).\n"
        "4. CLAUSULAZOS RENTABLES (GANGAS SIN LÍMITE): Si hay dinero en caja y un jugador rival es una ganga evidente (subida diaria fuerte, ratio cláusula/valor bajo ≤1.10x y amortización del sobrecoste en menos de 4 días), se pueden realizar todos los clausulazos que se quieran. Si el sobrecoste es alto o no es ganga, no se compra.\n"
        "5. FLEXIBILIDAD TÁCTICA: La alineación NO tiene por qué ser 3-4-3. Usa alinear_equipo() para probar todas las formaciones y elegir la mejor.\n"
        "6. SOLVENCIA ABSOLUTA: Prohibido gastar lo que no tienes en caja. Si necesitas dinero para fichar, primero vende a activos bajistas con aceptar_oferta_mercado(), comprueba tu nueva caja y luego ficha.\n"
        "7. VERACIDAD EN EL INFORME: En 'finalizar_sesion(resumen_tactico, nueva_memoria)', redacta el informe explicando ÚNICAMENTE las acciones que efectivamente llamaste y ejecutaste mediante las herramientas durante esta sesión. No inventes ventas ni fichajes que no hayas llamado con una función.\n\n"
        "PROTOCOLO DE ACCIÓN DEL AGENTE:\n"
        "- Paso 1: Llama a consultar_caja_y_plantilla() y evaluar_ofertas_recibidas().\n"
        "- Paso 2: Si hay ofertas lucrativas por activos bajistas, acéptalas con aceptar_oferta_mercado() para inyectar liquidez.\n"
        "- Paso 3: Con la caja fresca actualizada, busca clausulazos o mercado libre con buscar_clausulazos_viables() y ficha si es rentable.\n"
        "- Paso 4: Ajusta el equipo con alinear_equipo().\n"
        "- Paso 5: Cuando hayas completado tus operaciones, concluye SIEMPRE llamando a finalizar_sesion(resumen_tactico, nueva_memoria)."
    )

    # Initial user message
    conversation_contents = [
        {
            "role": "user",
            "parts": [
                {
                    "text": (
                        f"Iniciamos sesión del mánager. Fecha y hora actual en España: {now_spain_str}.\n"
                        f"Modo de ejecución: {'REAL (ejecuta en LaLiga Fantasy)' if execute else 'SIMULACIÓN (dry-run)'}.\n"
                        "Por favor, revisa la tesorería y las ofertas recibidas, toma las mejores decisiones de trading y alineación, y finaliza la sesión con el informe definitivo."
                    )
                }
            ]
        }
    ]

    final_report = ""
    final_memory = ""
    turns_executed = 0
    agent_steps_recorded = []

    # -------------------------------------------------------------
    # Agentic Execution Loop
    # -------------------------------------------------------------
    for step in range(1, MAX_AGENT_STEPS + 1):
        turns_executed = step
        print(f"\n--- 🔄 PASO AGÉNTICO {step}/{MAX_AGENT_STEPS} ---")

        try:
            response_content = _call_gemini_turn(
                contents=conversation_contents,
                system_instruction=system_instruction,
                tools_decl=tools_declaration,
                api_key=api_key,
                model=model
            )
        except Exception as e:
            print(f"[ERROR] Error al consultar a Gemini en el paso {step}: {e}")
            break

        parts = response_content.get("parts", [])
        function_call = None
        text_content = ""

        for part in parts:
            if "functionCall" in part:
                function_call = part["functionCall"]
            if "text" in part:
                text_content += part["text"] + "\n"

        if text_content.strip():
            print(f"🧠 Pensamiento Gemini:\n{text_content.strip()}")

        if not function_call:
            print("· El modelo ha finalizado su razonamiento textual.")
            final_report = text_content.strip()
            agent_steps_recorded.append({
                "step": step,
                "tool": "razonamiento_textual",
                "args": {},
                "thought": text_content.strip(),
                "result": {"mensaje": "Conclusión de sesión"}
            })
            break

        func_name = function_call.get("name")
        func_args = function_call.get("args", {})
        print(f"🛠️ Acción solicitada por Gemini: {func_name}({json.dumps(func_args, ensure_ascii=False)})")

        tool_result = {}
        if func_name == "consultar_caja_y_plantilla":
            tool_result = handle_consultar_caja()
        elif func_name == "evaluar_ofertas_recibidas":
            tool_result = handle_evaluar_ofertas()
        elif func_name == "aceptar_oferta_mercado":
            tool_result = handle_aceptar_oferta(func_args)
        elif func_name == "buscar_clausulazos_viables":
            tool_result = handle_buscar_clausulazos(func_args)
        elif func_name == "buscar_mercado_libre":
            tool_result = handle_buscar_mercado_libre(func_args)
        elif func_name == "ejecutar_clausulazo":
            tool_result = handle_ejecutar_clausulazo(func_args)
        elif func_name == "programar_puja_mercado":
            tool_result = handle_programar_puja(func_args)
        elif func_name == "alinear_equipo":
            tool_result = handle_alinear_equipo()
        elif func_name == "finalizar_sesion":
            final_report = func_args.get("resumen_tactico", "")
            final_memory = func_args.get("nueva_memoria", "")
            tool_result = {"status": "completado", "mensaje": "Sesión finalizada exitosamente."}
        else:
            tool_result = {"status": "error", "mensaje": f"Herramienta desconocida: {func_name}"}

        resp_dict = tool_result if isinstance(tool_result, dict) else {"resultado": tool_result}
        print(f"📥 Resultado enviado a Gemini: {json.dumps(resp_dict, ensure_ascii=False)[:200]}...")

        agent_steps_recorded.append({
            "step": step,
            "tool": func_name,
            "args": func_args,
            "thought": text_content.strip(),
            "result": resp_dict
        })

        user_parts = [
            {
                "functionResponse": {
                    "name": func_name,
                    "response": resp_dict
                }
            }
        ]
        if step == MAX_AGENT_STEPS - 1 and func_name != "finalizar_sesion":
            user_parts.append({
                "text": "AVISO DE CIERRE DE TURNO: Estás en el último paso disponible. Concluye tus decisiones y llama obligatoriamente a finalizar_sesion(resumen_tactico, nueva_memoria)."
            })

        conversation_contents.append({
            "role": "model",
            "parts": parts
        })
        conversation_contents.append({
            "role": "user",
            "parts": user_parts
        })

        if func_name == "finalizar_sesion":
            print("🏁 Sesión cerrada por Gemini mediante 'finalizar_sesion'.")
            break

        time.sleep(1.5)

    # If the session ended without explicit text report, request a clean wrap-up summary
    if not final_report:
        try:
            print("· Solicitando resumen final ejecutivo a Gemini...")
            wrap_contents = conversation_contents + [
                {
                    "role": "user",
                    "parts": [{"text": "Has completado tus acciones del día. Por favor, emite un resumen táctico estructurado de todas las operaciones realizadas, la situación de la tesorería y la estrategia para la jornada."}]
                }
            ]
            final_resp = _call_gemini_turn(wrap_contents, system_instruction, [], api_key, model=model)
            final_report = "".join(p.get("text", "") for p in final_resp.get("parts", []) if "text" in p).strip()
            if not final_memory:
                final_memory = f"Sesión agéntica completada con saldo final de {int(simulated_money):,} €."
        except Exception as e:
            print(f"  · Info wrap-up: {e}")

    # -------------------------------------------------------------
    # Post-Execution: Save Memory, History, and Generate Dashboard
    # -------------------------------------------------------------
    print("\n" + "=" * 60)
    print("📊 FINALIZANDO Y PUBLICANDO RESULTADOS DEL MODO AGÉNTICO")
    print("=" * 60)

    # Construir resumen verificado de acciones reales/simuladas
    ledger_str = ""
    if executed_actions_log:
        ledger_str = "### 📋 Registro de Acciones Ejecutadas:\n" + "\n".join(f"- {act}" for act in executed_actions_log) + "\n\n"
    else:
        ledger_str = "### 📋 Registro de Acciones Ejecutadas:\n- Sin movimientos directos de mercado en esta sesión.\n\n"

    full_report_text = ledger_str + (final_report or (
        f"Sesión completada en {turns_executed} pasos agénticos.\n"
        f"Saldo final en caja: {int(simulated_money):,} €.\n"
        f"Memoria: {final_memory}"
    ))

    if final_memory and execute:
        try:
            mem_path = os.path.join(config.ROOT, "hermes", "MEMORY.md")
            os.makedirs(os.path.dirname(mem_path), exist_ok=True)
            with open(mem_path, "w", encoding="utf-8") as f:
                f.write(f"# MEMORY\n\nÚltima actualización: {now_spain_str}\n\n{final_memory}\n")
            print("[OK] Memoria persistente actualizada en hermes/MEMORY.md")
        except Exception as e:
            print(f"  ✗ Error al guardar memoria: {e}")
    elif final_memory and not execute:
        print("[SIMULACIÓN] Memoria generada pero no persistida en hermes/MEMORY.md (ejecutar con --execute para guardar)")

    try:
        r_history = state.load_reasoning_history()
        entry = {
            "timestamp": now_spain_str,
            "date_str": now_spain_str,
            "reasoning": full_report_text,
            "response": full_report_text,
            "decision": {"modo": "agentico", "pasos": turns_executed, "nueva_memoria": final_memory},
            "steps": agent_steps_recorded
        }
        r_history = [entry] + [h for h in r_history if (h.get("reasoning") or h.get("response")) != full_report_text]
        state.save_reasoning_history(r_history)
        print("[OK] Historial de razonamiento persistido en .state/reasoning_history.json")
    except Exception as e:
        print(f"  ✗ Error al guardar historial: {e}")

    try:
        team = fc.team(lid, tid)
        market = fc.market(lid)
        league_teams = fc.league_teams(lid)
    except Exception:
        pass

    best_lineup = {}
    try:
        best_lineup = lineup_opt.optimize(team, prob_index, t_index) or {}
    except Exception:
        pass

    try:
        generate_apple_dashboard(
            team=team,
            market=market,
            best_lineup=best_lineup,
            flips=[],
            gaps=[],
            review_report="Modo Agéntico",
            gemini_response=full_report_text,
            decision={"nueva_memoria": final_memory},
            executed=execute,
            prob_index=prob_index,
            league_teams=league_teams,
            agentic_steps=agent_steps_recorded
        )
        print("[OK] Dashboard Web generado exitosamente con el informe del Modo Agéntico.")
    except Exception as e:
        print(f"  ✗ Error al generar dashboard: {e}")

    return {
        "pasos_ejecutados": turns_executed,
        "saldo_final": team.get("teamMoney", 0),
        "informe": full_report_text,
        "memoria": final_memory
    }
