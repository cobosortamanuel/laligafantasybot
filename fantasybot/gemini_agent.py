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


def call_gemini(prompt: str, system_prompt: str, api_key: str, model: str = DEFAULT_MODEL, thinking_budget: int = 1024) -> str:
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
    print("🤖 INICIANDO AGENTE FANTASYBOT CON GEMINI FLASH LITE")
    print("=" * 60)

    fc = FantasyClient()
    lid, tid = fc.default_ids()
    
    print("· Obteniendo estado del equipo y mercado...")
    team = fc.team(lid, tid)
    market = fc.market(lid)
    
    # Lineup optimization
    best_lineup = None
    try:
        best_lineup = lineup_opt.optimize(team)
    except Exception as e:
        print(f"· Aviso al calcular alineación óptima: {e}")

    # Market opportunities (flips)
    flips = []
    try:
        flips = [o for o in flip.opportunities(fc, lid) if o.get("margin_pct", 0) > 0][:8]
    except Exception as e:
        print(f"· Aviso al calcular flips: {e}")

    # Squad gaps
    gaps = needs_mod.gaps(team)
    
    # Read existing memory
    memory_path = os.path.join(config.ROOT, "hermes", "MEMORY.md")
    existing_memory = ""
    if os.path.exists(memory_path):
        with open(memory_path, "r", encoding="utf-8") as f:
            existing_memory = f.read()

    # Build prompt
    situation = {
        "fecha_hora": datetime.now().isoformat(),
        "presupuesto_disponible": team.get("teamMoney", 0),
        "valor_plantilla": team.get("teamValue", 0),
        "huecos_en_plantilla": gaps,
        "jugadores_en_plantilla": [
            {
                "id": p["playerMaster"]["id"],
                "nombre": p["playerMaster"].get("nickname") or p["playerMaster"].get("name"),
                "posicion": p["playerMaster"].get("positionId"),
                "valor": p.get("playerTeam", {}).get("marketValue") or p["playerMaster"].get("marketValue")
            } for p in team.get("players", [])
        ],
        "alineacion_optima_calculada": {
            "formacion": best_lineup.get("formation") if best_lineup else None,
            "incompleta": best_lineup.get("incomplete") if best_lineup else True,
            "total_score": best_lineup.get("total") if best_lineup else 0
        },
        "oportunidades_flip_mercado": [
            {
                "marketId": f.get("marketId"),
                "nombre": f.get("nombre"),
                "precio": f.get("precio"),
                "margen_pct": f.get("margin_pct"),
                "subida_diaria": f.get("subida_diaria")
            } for f in flips
        ]
    }

    system_prompt = (
        "Eres el Director Deportivo y Mánager de IA de un equipo en LALIGA Fantasy.\n"
        "Tu misión es tomar las mejores decisiones para maximizar puntos y aumentar el valor del equipo.\n"
        "Analiza la situación que te presentamos y responde con:\n"
        "1. Un breve análisis estratégico en español (3-5 líneas).\n"
        "2. Tus recomendaciones concretas (qué hacer con la alineación y qué pujas hacer).\n"
        "3. Una sección final con formato JSON estricto con las acciones a realizar:\n"
        "```json\n"
        "{\n"
        '  "aplicar_alineacion": true,\n'
        '  "pujas_recomendadas": [\n'
        '    {"marketId": 12345, "nombre": "Nombre", "puja_maxima": 1500000}\n'
        "  ],\n"
        '  "nueva_memoria": "Breve nota actualizada para recordar en futuras revisiones."\n'
        "}\n"
        "```"
    )

    user_prompt = (
        f"Memoria previa del mánager:\n{existing_memory}\n\n"
        f"Estado actual de la liga y equipo:\n{json.dumps(situation, ensure_ascii=False, indent=2)}\n\n"
        "Razona y decide qué movimientos debemos hacer hoy."
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
                if decision.get("aplicar_alineacion") and best_lineup:
                    try:
                        current_ids = []
                        execute_mod.apply_lineup(fc, tid, best_lineup, current_ids, dry_run=False)
                        print("  ✓ Alineación óptima aplicada en el juego.")
                    except Exception as e:
                        print(f"  ✗ No se pudo aplicar alineación: {e}")

                for bid_item in decision.get("pujas_recomendadas", []):
                    m_id = bid_item.get("marketId")
                    max_bid = bid_item.get("puja_maxima")
                    if m_id and max_bid:
                        try:
                            fc.bid(m_id, int(max_bid))
                            print(f"  ✓ Puja enviada por {bid_item.get('nombre', m_id)}: {max_bid:,} €")
                        except Exception as e:
                            print(f"  ✗ Error al pujar por {bid_item.get('nombre', m_id)}: {e}")
            else:
                print("\n(Modo simulación: no se han enviado cambios a LaLiga Fantasy. Usa --execute para aplicarlos).")

    except Exception as e:
        print(f"[ERROR] Error al llamar a Gemini: {e}")
