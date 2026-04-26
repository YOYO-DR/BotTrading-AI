"""
MT5 AI Trading Agent
────────────────────
Lanza un modelo LLM via LiteLLM, se conecta al MCP de MetaTrader 5,
valida posiciones abiertas, analiza velas 4H/1H/15min, decide si hay
entrada válida y guarda cada operación en un archivo de memoria JSON.

Uso:
    python mt5_agent.py

Dependencias:
  pip install openai mcp

Variables de entorno necesarias:
    MT5_LOGIN, MT5_PASSWORD, MT5_SERVER
    + la API key del proveedor LLM (ej: ANTHROPIC_API_KEY, OPENAI_API_KEY)
"""

import asyncio
import json
import os
import shutil
import sys
from contextlib import AsyncExitStack
from datetime import datetime
from typing import Any

from dotenv import load_dotenv
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from openai import OpenAI

load_dotenv()

# ──────────────────────────────────────────────────────────────
# CONFIGURACIÓN  ← Edita estos valores
# ──────────────────────────────────────────────────────────────
MODEL = os.getenv("MODEL", "")   # cualquier modelo soportado por litellm

MEMORY_FILE = "trade_memory.json"       # historial de operaciones
STRATEGY_FILE = "strategy.md"           # prompt del sistema en markdown
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")
BASE_URL = os.getenv("BASE_URL", "")
MCP_CONNECT_TIMEOUT_SEC = float(os.getenv("MCP_CONNECT_TIMEOUT_SEC", "30"))
LLM_REQUEST_TIMEOUT_SEC = float(os.getenv("LLM_REQUEST_TIMEOUT_SEC", "90"))
LLM_USER_AGENT = os.getenv("LLM_USER_AGENT", "curl/8.17.0")

# pares CRT (Gold, NQ, Forex)
SYMBOLS = [
  # "XAUUSD", 
  # "NAS100", 
  "EURUSD", 
  # "GBPUSD"
  ]
# D1 = Daily Bias | H4 = setup CRT | M15 = entrada
TIMEFRAMES = ["D1", "H4", "M15"]
MCP_SERVER_COMMAND = os.getenv("MCP_SERVER_COMMAND", "metatrader-mcp-server")

# Configuración del servidor MCP de MetaTrader 5
# (usa el paquete: pip install metatrader-mcp-server)
MCP_SERVER = StdioServerParameters(
  command=MCP_SERVER_COMMAND,
    args=[
        "--login", os.getenv("MT5_LOGIN", "TU_LOGIN"),
        "--password", os.getenv("MT5_PASSWORD", "TU_PASSWORD"),
        "--server", os.getenv("MT5_SERVER", "TU_BROKER_SERVER"),
        "--transport", "stdio",
        "--path", r"C:\Program Files\XM Global MT5\terminal64.exe",
    ],
    env=None,
)

MAX_AGENT_ITERATIONS = 40   # límite de seguridad para el loop
_OPENAI_CLIENT: OpenAI | None = None

# ──────────────────────────────────────────────────────────────
# PROMPT DEL SISTEMA
# ──────────────────────────────────────────────────────────────


def load_system_prompt() -> str:
  """Carga el prompt del sistema desde strategy.md."""
  strategy_path = os.path.join(os.path.dirname(__file__), STRATEGY_FILE)
  try:
    with open(strategy_path, "r", encoding="utf-8") as f:
      return f.read().strip()
  except OSError as exc:
    raise RuntimeError(
      f"No se pudo leer el prompt de estrategia en '{strategy_path}': {exc}"
        ) from exc

# ──────────────────────────────────────────────────────────────
# MEMORIA DE OPERACIONES
# ──────────────────────────────────────────────────────────────


def load_memory() -> list[dict]:
  """Carga el historial de operaciones desde el archivo JSON."""
  if not os.path.exists(MEMORY_FILE):
    return []
  try:
    with open(MEMORY_FILE, "r", encoding="utf-8") as f:
      return json.load(f)
  except (json.JSONDecodeError, OSError):
    return []


def save_trade(trade_data: dict) -> None:
  """Guarda una operación ejecutada en la memoria persistente."""
  memory = load_memory()
  trade_data["saved_at"] = datetime.now().isoformat()
  memory.append(trade_data)
  with open(MEMORY_FILE, "w", encoding="utf-8") as f:
    json.dump(memory, f, indent=2, ensure_ascii=False)
  print(f"\n💾  Operación guardada en {MEMORY_FILE}")
  print(f"    Ticket: {trade_data.get('ticket', 'N/A')} | "
        f"{trade_data.get('symbol')} {trade_data.get('direction')}")


def format_memory_for_prompt(memory: list[dict]) -> str:
  """Formatea la memoria como texto para incluir en el prompt."""
  if not memory:
    return "No hay operaciones previas registradas."

  lines = [f"Se han registrado {len(memory)} operaciones previas:\n"]
  for t in memory[-10:]:   # solo las últimas 10 para no saturar el contexto
    lines.append(
        f"• [{t.get('saved_at', '?')[:16]}] "
        f"Ticket {t.get('ticket', '?')} | "
        f"{t.get('symbol', '?')} {t.get('direction', '?')} | "
        f"Razón: {t.get('reason', '?')[:80]} | "
        f"Expectativa: {t.get('expectation', '?')[:80]}"
    )
  return "\n".join(lines)


# ──────────────────────────────────────────────────────────────
# CONVERSIÓN MCP TOOLS → FORMATO LITELLM (OpenAI)
# ──────────────────────────────────────────────────────────────

def mcp_tools_to_litellm(mcp_tools) -> list[dict]:
  """
  Convierte la lista de herramientas MCP al formato
  de tools que espera LiteLLM / OpenAI.
  """
  result = []
  for tool in mcp_tools:
    input_schema = tool.inputSchema if hasattr(tool, "inputSchema") else {}
    result.append({
        "type": "function",
        "function": {
            "name": tool.name,
            "description": tool.description or "",
            "parameters": input_schema,
        },
    })
  return result


def get_openai_client() -> OpenAI:
  """Inicializa cliente OpenAI compatible con base_url custom (LiteLLM proxy)."""
  global _OPENAI_CLIENT
  if _OPENAI_CLIENT is not None:
    return _OPENAI_CLIENT

  if not BASE_URL:
    raise RuntimeError(
      "Falta BASE_URL. Debe incluir /v1 de tu endpoint OpenAI-compatible."
    )

  _OPENAI_CLIENT = OpenAI(
      api_key=OPENAI_API_KEY,
      base_url=BASE_URL,
      timeout=LLM_REQUEST_TIMEOUT_SEC,
      default_headers={
        "User-Agent": LLM_USER_AGENT,
        "Accept": "*/*",
      },
  )
  return _OPENAI_CLIENT


def call_model_with_openai_sdk(
    model: str,
    messages: list[dict],
    tools: list[dict],
) -> tuple[dict, str | None]:
  """Llama al modelo usando el SDK OpenAI contra endpoint compatible."""
  client = get_openai_client()
  try:
    response = client.chat.completions.create(
        model=model,
        messages=messages,
        tools=tools,
        tool_choice="auto",
    )
  except Exception as exc:
    raise RuntimeError(f"Error llamando al modelo via SDK OpenAI: {exc}") from exc

  if not response.choices:
    raise RuntimeError("Respuesta inválida del modelo: no contiene choices.")

  choice = response.choices[0]
  message = choice.message.model_dump(exclude_none=True)
  finish_reason = choice.finish_reason
  return message, finish_reason


# ──────────────────────────────────────────────────────────────
# EXTRACCIÓN DEL JSON DE DECISIÓN
# ──────────────────────────────────────────────────────────────

def extract_decision(text: str) -> dict | None:
  """Extrae el bloque JSON de decisión del texto del modelo."""
  import re
  # Busca bloque ```json ... ``` o JSON suelto al final
  pattern = r"```json\s*(\{.*?\})\s*```"
  match = re.search(pattern, text, re.DOTALL)
  if match:
    try:
      return json.loads(match.group(1))
    except json.JSONDecodeError:
      pass

  # Último intento: busca { ... } al final del texto
  last_brace = text.rfind("{")
  if last_brace != -1:
    try:
      return json.loads(text[last_brace:])
    except json.JSONDecodeError:
      pass

  return None


# ──────────────────────────────────────────────────────────────
# AGENTE PRINCIPAL
# ──────────────────────────────────────────────────────────────

async def run_agent() -> None:
  """
  Loop principal del agente:
  1. Conecta al MCP de MT5
  2. Envía prompt inicial al modelo con las herramientas disponibles
  3. Ejecuta tool calls hasta que el modelo tome una decisión final
  4. Guarda la operación si se ejecutó
  """
  print("=" * 60)
  print("🤖  MT5 AI Trading Agent")
  print(f"    Modelo : {MODEL}")
  print(f"    Símbolos: {', '.join(SYMBOLS)}")
  print(f"    Timeframes: {', '.join(TIMEFRAMES)}")
  print("=" * 60)

  if not OPENAI_API_KEY:
    raise RuntimeError("Falta OPENAI_API_KEY en variables de entorno o .env")

  if shutil.which(MCP_SERVER_COMMAND) is None:
    raise RuntimeError(
      "No se encontró el ejecutable del servidor MCP: "
      f"'{MCP_SERVER_COMMAND}'. Instala el paquete 'metatrader-mcp-server' "
      "en este entorno o define MCP_SERVER_COMMAND con la ruta/comando correcto."
    )

  memory = load_memory()
  memory_text = format_memory_for_prompt(memory)

  system_prompt = load_system_prompt().format(memory=memory_text)
  user_message = (
      f"Opera la estrategia CRT (Candle Range Theory) de Cluti Fx para los siguientes activos: "
      f"{', '.join(SYMBOLS)}.\n\n"
      "FLUJO OBLIGATORIO:\n"
      "1. Revisa las posiciones abiertas con la herramienta correspondiente.\n"
      f"2. Para cada activo obtén velas de {', '.join(TIMEFRAMES)}.\n"
      "3. Determina el Daily Bias en D1 (alcista / bajista / ambiguo).\n"
      "4. Si el bias está definido, identifica el setup CRT en H4 (velas 1am/5am EST de referencia).\n"
      "5. Baja a M15 para confirmar la Vela 3 (cierre dentro del rango) y buscar confluencias "
      "(Order Block, FVG, Killzone).\n"
      "6. Si todos los filtros pasan, ejecuta la operación con SL y TP definidos (RR >= 2).\n"
      "7. Responde con el bloque JSON de decisión."
  )

  messages = [
      {"role": "user", "content": user_message},
  ]

  async with AsyncExitStack() as stack:
    # ── Conexión al MCP ────────────────────────────────────
    print("\n🔌  Conectando al servidor MCP de MetaTrader 5...")
    try:
      read, write = await asyncio.wait_for(
        stack.enter_async_context(stdio_client(MCP_SERVER)),
        timeout=MCP_CONNECT_TIMEOUT_SEC,
      )
      session: ClientSession = await asyncio.wait_for(
        stack.enter_async_context(ClientSession(read, write)),
        timeout=MCP_CONNECT_TIMEOUT_SEC,
      )
      await asyncio.wait_for(
        session.initialize(),
        timeout=MCP_CONNECT_TIMEOUT_SEC,
      )
    except TimeoutError as exc:
      raise RuntimeError(
        "Timeout conectando al MCP de MetaTrader 5. "
        "El servidor no respondió a tiempo. Revisa que MT5 esté abierto, "
        "logueado y que no haya instancias colgadas de scheduler/mt5_agent. "
        "Puedes ajustar MCP_CONNECT_TIMEOUT_SEC en .env."
      ) from exc

    # ── Obtener herramientas disponibles ──────────────────
    tools_response = await session.list_tools()
    available_tools = tools_response.tools
    litellm_tools = mcp_tools_to_litellm(available_tools)

    print(f"✅  MCP conectado. Herramientas disponibles: "
          f"{[t.name for t in available_tools]}\n")

    # ── Agentic Loop ───────────────────────────────────────
    iteration = 0
    final_decision = None

    while iteration < MAX_AGENT_ITERATIONS:
      iteration += 1
      print(f"── Iteración {iteration} {'─' * 40}")

      # Llamada al modelo
      msg, finish_reason = call_model_with_openai_sdk(
          model=MODEL,
          messages=[{"role": "system", "content": system_prompt}] + messages,
          tools=litellm_tools,
      )

      # Agregar respuesta del asistente al historial
      assistant_msg: dict[str, Any] = {
          "role": "assistant",
          "content": msg.get("content")
      }
      if msg.get("tool_calls"):
        assistant_msg["tool_calls"] = msg["tool_calls"]
      messages.append(assistant_msg)

      # ── El modelo decidió terminar (sin más tool calls) ─
      tool_calls = msg.get("tool_calls") or []
      if finish_reason in ("stop", "end_turn") or not tool_calls:
        content_text = msg.get("content") or ""
        print(f"\n📋  Respuesta final del modelo:\n{content_text}\n")

        # Extraer decisión JSON
        final_decision = extract_decision(content_text)

        if final_decision:
          decision_type = final_decision.get("decision", "UNKNOWN")
          print(f"🏁  DECISIÓN: {decision_type}")
          if decision_type == "TRADE":
            save_trade(final_decision)
          else:
            print(
              f"⛔  Sin entrada. Razón: {final_decision.get('reason', 'N/A')}")
        else:
          print("⚠️  No se encontró bloque de decisión JSON en la respuesta.")

        break   # ← Salir del loop

      # ── Ejecutar tool calls ────────────────────────────
      tool_results = []
      for tool_call in tool_calls:
        fn_name = tool_call["function"]["name"]
        fn_args_raw = tool_call["function"].get("arguments", "{}")
        fn_args = json.loads(fn_args_raw) if isinstance(
          fn_args_raw, str) else fn_args_raw

        print(f"🔧  Llamando herramienta: {fn_name}")
        print(f"    Args: {json.dumps(fn_args, ensure_ascii=False)[:200]}")

        try:
          result = await session.call_tool(fn_name, arguments=fn_args)
          # Convertir resultado MCP a texto
          result_text = " ".join(
              block.text if hasattr(block, "text") else str(block)
              for block in result.content
          )
          print(f"    ✓ Resultado ({len(result_text)} chars)")
        except Exception as exc:
          result_text = f"ERROR al ejecutar {fn_name}: {exc}"
          print(f"    ✗ {result_text}")

        tool_results.append({
            "role": "tool",
          "tool_call_id": tool_call["id"],
            "content": result_text,
        })

      messages.extend(tool_results)

    else:
      print(
        f"\n⚠️  Se alcanzó el límite de {MAX_AGENT_ITERATIONS} iteraciones sin decisión final.")

  print("\n" + "=" * 60)
  print("✅  Ejecución del agente completada.")
  if final_decision:
    print(f"    Decisión: {final_decision.get('decision')} | "
          f"Símbolo: {final_decision.get('symbol', 'N/A')}")
  print("=" * 60 + "\n")


# ──────────────────────────────────────────────────────────────
# ENTRY POINT
# ──────────────────────────────────────────────────────────────

if __name__ == "__main__":
  asyncio.run(run_agent())
