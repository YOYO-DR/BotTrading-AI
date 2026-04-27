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
import logging
import os
import shutil
import sys
from contextlib import AsyncExitStack
from datetime import datetime, time, timezone
from typing import Any
import re

from dotenv import load_dotenv
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from openai import OpenAI

load_dotenv()
log = logging.getLogger("mt5_agent")

# ──────────────────────────────────────────────────────────────
# CONFIGURACIÓN  ← Edita estos valores
# ──────────────────────────────────────────────────────────────
MODEL = os.getenv("MODEL", "")   # cualquier modelo soportado por litellm

MEMORY_FILE = "trade_memory.json"       # historial de operaciones
STRATEGY_FILE = "strategy.md"           # prompt del sistema en markdown
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")
BASE_URL = os.getenv("BASE_URL", "")
CALL_TIMEOUT_SEC = float(os.getenv("CALL_TIMEOUT_SEC", "100"))
MCP_CONNECT_TIMEOUT_SEC = float(os.getenv("MCP_CONNECT_TIMEOUT_SEC", str(CALL_TIMEOUT_SEC)))
LLM_REQUEST_TIMEOUT_SEC = float(os.getenv("LLM_REQUEST_TIMEOUT_SEC", str(CALL_TIMEOUT_SEC)))
MCP_TOOL_TIMEOUT_SEC = float(os.getenv("MCP_TOOL_TIMEOUT_SEC", str(CALL_TIMEOUT_SEC)))
LLM_USER_AGENT = os.getenv("LLM_USER_AGENT", "curl/8.17.0")
TRADE_LOT_RAW = os.getenv("TRADE_LOT", "0.01")
EXECUTION_WINDOWS_UTC_RAW = os.getenv("EXECUTION_WINDOWS_UTC", "07:00-11:00,13:30-17:00")
EXECUTION_WINDOWS_COT_RAW = os.getenv("EXECUTION_WINDOWS_COT", "").strip()
EXECUTION_COT_UTC_OFFSET_HOURS = float(os.getenv("EXECUTION_COT_UTC_OFFSET_HOURS", "-5"))
ENFORCE_EXECUTION_WINDOWS = os.getenv("ENFORCE_EXECUTION_WINDOWS", "true").strip().lower() in (
  "1", "true", "yes", "on"
)

# pares CRT (Gold, NQ, Forex)
DEFAULT_SYMBOLS = ["EURUSD"]
SYMBOLS_RAW = os.getenv("SYMBOLS", "").strip()
if SYMBOLS_RAW:
  SYMBOLS = [symbol.strip() for symbol in SYMBOLS_RAW.split(",") if symbol.strip()]
  if not SYMBOLS:
    raise RuntimeError(
      "SYMBOLS inválido: no se encontraron símbolos válidos. "
      "Formato esperado: SYMBOLS=EURUSD,GBPUSD,GOLD#"
    )
else:
  SYMBOLS = DEFAULT_SYMBOLS

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
  log.info("\n💾  Operación guardada en %s", MEMORY_FILE)
  log.info(
    "    Ticket: %s | %s %s",
    trade_data.get("ticket", "N/A"),
    trade_data.get("symbol"),
    trade_data.get("direction"),
  )


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


def format_memory_for_symbol(memory: list[dict], symbol: str) -> str:
  """Formatea memoria histórica únicamente del símbolo objetivo."""
  symbol_upper = symbol.upper()
  symbol_memory = [
    trade for trade in memory
    if str(trade.get("symbol", "")).upper() == symbol_upper
  ]

  if not symbol_memory:
    return f"No hay operaciones previas registradas para {symbol}."

  lines = [
    f"Se han registrado {len(symbol_memory)} operaciones previas para {symbol}:\n"
  ]
  for t in symbol_memory[-10:]:
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


def _parse_hhmm(value: str) -> time:
  """Parsea una hora HH:MM."""
  value = value.strip()
  try:
    dt = datetime.strptime(value, "%H:%M")
  except ValueError as exc:
    raise RuntimeError(f"Hora inválida '{value}'. Formato esperado HH:MM.") from exc
  return dt.time()


def parse_execution_windows(raw: str, var_name: str) -> list[tuple[time, time]]:
  """Convierte 'HH:MM-HH:MM,HH:MM-HH:MM' en lista de ventanas."""
  windows: list[tuple[time, time]] = []
  chunks = [chunk.strip() for chunk in raw.split(",") if chunk.strip()]
  if not chunks:
    raise RuntimeError(f"{var_name} no puede estar vacío.")

  for chunk in chunks:
    if "-" not in chunk:
      raise RuntimeError(
        f"Ventana inválida en {var_name}: '{chunk}'. Debe tener formato HH:MM-HH:MM."
      )
    start_raw, end_raw = chunk.split("-", 1)
    start = _parse_hhmm(start_raw)
    end = _parse_hhmm(end_raw)
    windows.append((start, end))
  return windows


def _minutes_since_midnight(value: time) -> int:
  return value.hour * 60 + value.minute


def _time_from_minutes(value: int) -> time:
  value = value % (24 * 60)
  return time(hour=value // 60, minute=value % 60)


def shift_time(value: time, delta_minutes: int) -> time:
  """Desplaza una hora por minutos con wrap 24h."""
  return _time_from_minutes(_minutes_since_midnight(value) + delta_minutes)


def convert_windows_to_utc(
    windows: list[tuple[time, time]],
    source_utc_offset_hours: float,
) -> list[tuple[time, time]]:
  """Convierte ventanas desde zona local (offset UTC) a UTC."""
  # Si local = UTC + offset, entonces UTC = local - offset.
  delta_minutes = int(round(-source_utc_offset_hours * 60))
  return [
    (shift_time(start, delta_minutes), shift_time(end, delta_minutes))
    for start, end in windows
  ]


def is_time_in_windows_utc(now_time: time, windows: list[tuple[time, time]]) -> bool:
  """Evalúa si la hora UTC actual cae dentro de al menos una ventana."""
  for start, end in windows:
    if start <= end:
      if start <= now_time <= end:
        return True
    else:
      # Ventana que cruza medianoche, ej: 22:00-02:00.
      if now_time >= start or now_time <= end:
        return True
  return False


def format_windows_utc(windows: list[tuple[time, time]]) -> str:
  """Formatea ventanas UTC para logging/prompt."""
  return ", ".join(f"{start.strftime('%H:%M')}-{end.strftime('%H:%M')}" for start, end in windows)


def get_trade_lot() -> float:
  """Obtiene y valida el lotaje fijo desde variable de entorno."""
  try:
    lot = float(TRADE_LOT_RAW)
  except ValueError as exc:
    raise RuntimeError(
      f"TRADE_LOT inválido: '{TRADE_LOT_RAW}'. Debe ser numérico (ej: 0.01)."
    ) from exc

  if lot <= 0:
    raise RuntimeError(f"TRADE_LOT debe ser > 0. Valor recibido: {lot}")

  return lot


def enforce_fixed_lot(fn_args: Any, fixed_lot: float) -> tuple[dict[str, Any], bool]:
  """Fuerza el lotaje fijo para órdenes, ignorando cualquier lote propuesto por el modelo."""
  if not isinstance(fn_args, dict):
    return {"volume": fixed_lot}, True

  overridden = False
  lot_keys = ("volume", "lot", "lots", "lot_size", "size")

  for key in lot_keys:
    if key in fn_args:
      previous = fn_args.get(key)
      fn_args[key] = fixed_lot
      if previous != fixed_lot:
        overridden = True

  if "volume" not in fn_args:
    fn_args["volume"] = fixed_lot
    overridden = True

  return fn_args, overridden


def _extract_positive_int(value: Any) -> int | None:
  """Devuelve entero positivo si el valor puede interpretarse como ticket."""
  if isinstance(value, bool) or value is None:
    return None
  if isinstance(value, int):
    return value if value > 0 else None
  if isinstance(value, float):
    int_value = int(value)
    return int_value if int_value > 0 else None
  if isinstance(value, str):
    value = value.strip()
    if value.isdigit():
      int_value = int(value)
      return int_value if int_value > 0 else None
  return None


def _find_ticket_in_obj(obj: Any) -> int | None:
  """Busca ticket recursivamente en estructuras JSON comunes."""
  if isinstance(obj, dict):
    for key in ("ticket", "order_ticket", "position_ticket", "deal_ticket"):
      ticket = _extract_positive_int(obj.get(key))
      if ticket:
        return ticket
    for value in obj.values():
      ticket = _find_ticket_in_obj(value)
      if ticket:
        return ticket
    return None
  if isinstance(obj, list):
    for item in obj:
      ticket = _find_ticket_in_obj(item)
      if ticket:
        return ticket
  return None


def extract_ticket_from_tool_result(result_text: str) -> int | None:
  """Extrae ticket desde texto de resultado MCP (JSON o texto plano)."""
  # 1) Intentar parseo JSON completo
  try:
    parsed = json.loads(result_text)
    ticket = _find_ticket_in_obj(parsed)
    if ticket:
      return ticket
  except Exception:
    pass

  # 2) Intentar encontrar fragmentos JSON dentro del texto
  for candidate in re.findall(r"\{[^{}]*\}", result_text):
    try:
      parsed = json.loads(candidate)
      ticket = _find_ticket_in_obj(parsed)
      if ticket:
        return ticket
    except Exception:
      continue

  # 3) Fallback regex simple
  match = re.search(r"(?i)ticket\D{0,10}(\d{3,})", result_text)
  if match:
    return _extract_positive_int(match.group(1))
  return None


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


def build_user_message_for_symbol(
    symbol: str,
    now_utc: datetime,
    execution_windows: list[tuple[time, time]],
    fixed_lot: float,
) -> str:
  """Construye el prompt de usuario para un único símbolo."""
  return (
      f"Opera la estrategia CRT (Candle Range Theory) de Cluti Fx para este activo: "
      f"{symbol}.\n\n"
      f"Hora actual UTC: {now_utc.strftime('%Y-%m-%d %H:%M:%S')}.\n"
      f"Ventanas operativas UTC configuradas: {format_windows_utc(execution_windows)}.\n\n"
      "FLUJO OBLIGATORIO:\n"
      "1. Revisa las posiciones abiertas con la herramienta correspondiente.\n"
      f"2. Para este activo obtén velas de {', '.join(TIMEFRAMES)}.\n"
      "3. Determina el Daily Bias en D1 (alcista / bajista / ambiguo).\n"
      "4. Si el bias está definido, identifica el setup CRT en H4 (velas 1am/5am EST de referencia).\n"
      "5. Baja a M15 para confirmar la Vela 3 (cierre dentro del rango) y buscar confluencias "
      "(Order Block, FVG, Killzone).\n"
      "6. Si todos los filtros pasan, ejecuta la operación con SL y TP definidos (RR >= 2) "
      "usando place_market_order o place_pending_order.\n"
      f"7. LOTAJE FIJO OBLIGATORIO: usa exactamente {fixed_lot} lotes. "
      "No propongas ni uses otro volumen.\n"
      "8. SOLO devuelve decision=TRADE si la orden fue ejecutada y tienes ticket real (>0). "
      "Si no hay ticket válido, devuelve NO_ENTRY.\n"
      "9. No mezcles análisis ni velas con otros símbolos. Este contexto es exclusivo de este activo.\n"
      "10. Responde con el bloque JSON de decisión."
  )


async def run_symbol_agent_loop(
    session: ClientSession,
    symbol: str,
    now_utc: datetime,
    execution_windows: list[tuple[time, time]],
    system_prompt: str,
    litellm_tools: list[dict],
    fixed_lot: float,
) -> dict | None:
  """Ejecuta un loop de decisión completo del modelo para un único símbolo."""
  user_message = build_user_message_for_symbol(
    symbol=symbol,
    now_utc=now_utc,
    execution_windows=execution_windows,
    fixed_lot=fixed_lot,
  )

  messages: list[dict[str, Any]] = [{"role": "user", "content": user_message}]
  iteration = 0
  final_decision = None
  executed_order_ticket: int | None = None
  attempted_order_placement = False

  while iteration < MAX_AGENT_ITERATIONS:
    iteration += 1
    log.info("── %s | Iteración %s %s", symbol, iteration, "─" * 30)

    # Llamada al modelo para este símbolo
    msg, finish_reason = call_model_with_openai_sdk(
      model=MODEL,
      messages=[{"role": "system", "content": system_prompt}] + messages,
      tools=litellm_tools,
    )

    assistant_msg: dict[str, Any] = {
      "role": "assistant",
      "content": msg.get("content"),
    }
    if msg.get("tool_calls"):
      assistant_msg["tool_calls"] = msg["tool_calls"]
    messages.append(assistant_msg)

    tool_calls = msg.get("tool_calls") or []
    if finish_reason in ("stop", "end_turn") or not tool_calls:
      content_text = msg.get("content") or ""
      log.info("\n📋  [%s] Respuesta final del modelo:\n%s\n", symbol, content_text)

      final_decision = extract_decision(content_text)

      if final_decision:
        final_decision.setdefault("symbol", symbol)
        decision_type = final_decision.get("decision", "UNKNOWN")
        log.info("🏁  [%s] DECISIÓN: %s", symbol, decision_type)

        if decision_type == "TRADE":
          model_ticket = _extract_positive_int(final_decision.get("ticket"))
          resolved_ticket = model_ticket or executed_order_ticket

          if resolved_ticket:
            final_decision["ticket"] = resolved_ticket
            save_trade(final_decision)
          else:
            log.warning(
              "⚠️  [%s] El modelo devolvió TRADE sin ticket real. No se guardará como operación ejecutada.",
              symbol,
            )
            if attempted_order_placement:
              log.warning(
                "⚠️  [%s] Hubo intento de colocar orden, pero no se obtuvo ticket válido en la respuesta del broker.",
                symbol,
              )
            else:
              log.warning(
                "⚠️  [%s] No hubo llamada a place_market_order/place_pending_order. "
                "Esto indica análisis hipotético, no ejecución real.",
                symbol,
              )
        else:
          log.info("⛔  [%s] Sin entrada. Razón: %s", symbol, final_decision.get("reason", "N/A"))
      else:
        log.warning("⚠️  [%s] No se encontró bloque de decisión JSON en la respuesta.", symbol)

      return final_decision

    tool_results = []
    for tool_call in tool_calls:
      fn_name = tool_call["function"]["name"]
      fn_args_raw = tool_call["function"].get("arguments", "{}")
      fn_args = json.loads(fn_args_raw) if isinstance(fn_args_raw, str) else fn_args_raw

      if fn_name in ("place_market_order", "place_pending_order"):
        fn_args, lot_overridden = enforce_fixed_lot(fn_args, fixed_lot)
        if lot_overridden:
          log.info(
            "    [%s] ℹ️ Lote forzado por configuración TRADE_LOT=%s (se ignoró el lote propuesto por el modelo).",
            symbol,
            fixed_lot,
          )

      log.info("🔧  [%s] Llamando herramienta: %s", symbol, fn_name)
      log.info("    [%s] Args: %s", symbol, json.dumps(fn_args, ensure_ascii=False)[:200])

      try:
        result = await asyncio.wait_for(
          session.call_tool(fn_name, arguments=fn_args),
          timeout=MCP_TOOL_TIMEOUT_SEC,
        )
        result_text = " ".join(
          block.text if hasattr(block, "text") else str(block)
          for block in result.content
        )
        log.info("    [%s] ✓ Resultado (%s chars)", symbol, len(result_text))

        if fn_name in ("place_market_order", "place_pending_order"):
          attempted_order_placement = True
          parsed_ticket = extract_ticket_from_tool_result(result_text)
          if parsed_ticket:
            executed_order_ticket = parsed_ticket
            log.info("    [%s] ✓ Ticket detectado en ejecución real: %s", symbol, parsed_ticket)
          else:
            log.warning("    [%s] ⚠️ Orden enviada pero no se pudo extraer ticket del resultado.", symbol)
      except Exception as exc:
        result_text = f"ERROR al ejecutar {fn_name}: {exc}"
        log.error("    [%s] ✗ %s", symbol, result_text)

      tool_results.append({
        "role": "tool",
        "tool_call_id": tool_call["id"],
        "content": result_text,
      })

    messages.extend(tool_results)

  log.warning(
    "\n⚠️  [%s] Se alcanzó el límite de %s iteraciones sin decisión final.",
    symbol,
    MAX_AGENT_ITERATIONS,
  )
  return final_decision


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
  log.info("=" * 60)

  now_utc = datetime.now(timezone.utc)
  if EXECUTION_WINDOWS_COT_RAW:
    execution_windows_cot = parse_execution_windows(
      EXECUTION_WINDOWS_COT_RAW,
      "EXECUTION_WINDOWS_COT",
    )
    execution_windows = convert_windows_to_utc(
      execution_windows_cot,
      EXECUTION_COT_UTC_OFFSET_HOURS,
    )
    log.info(
      "    Ventanas ejecución COT: %s (offset UTC %s)",
      format_windows_utc(execution_windows_cot),
      EXECUTION_COT_UTC_OFFSET_HOURS,
    )
  else:
    execution_windows = parse_execution_windows(
      EXECUTION_WINDOWS_UTC_RAW,
      "EXECUTION_WINDOWS_UTC",
    )

  log.info("    Hora UTC actual: %s", now_utc.strftime("%Y-%m-%d %H:%M:%S UTC"))
  log.info("    Ventanas ejecución UTC: %s", format_windows_utc(execution_windows))

  if ENFORCE_EXECUTION_WINDOWS and not is_time_in_windows_utc(now_utc.time(), execution_windows):
    log.info(
      "⏸️  Fuera de ventana operativa UTC (%s). Se omite esta ejecución.",
      format_windows_utc(execution_windows),
    )
    return
  log.info("🤖  MT5 AI Trading Agent")
  log.info("    Modelo : %s", MODEL)
  log.info("    Símbolos: %s", ", ".join(SYMBOLS))
  log.info("    Timeframes: %s", ", ".join(TIMEFRAMES))
  log.info(
    "    Timeouts(s) -> default:%s | mcp_connect:%s | mcp_tool:%s | llm_request:%s",
    CALL_TIMEOUT_SEC,
    MCP_CONNECT_TIMEOUT_SEC,
    MCP_TOOL_TIMEOUT_SEC,
    LLM_REQUEST_TIMEOUT_SEC,
  )
  log.info("=" * 60)

  if not OPENAI_API_KEY:
    raise RuntimeError("Falta OPENAI_API_KEY en variables de entorno o .env")

  fixed_lot = get_trade_lot()

  if shutil.which(MCP_SERVER_COMMAND) is None:
    raise RuntimeError(
      "No se encontró el ejecutable del servidor MCP: "
      f"'{MCP_SERVER_COMMAND}'. Instala el paquete 'metatrader-mcp-server' "
      "en este entorno o define MCP_SERVER_COMMAND con la ruta/comando correcto."
    )

  memory = load_memory()
  system_prompt_template = load_system_prompt()
  async with AsyncExitStack() as stack:
    # ── Conexión al MCP ────────────────────────────────────
    log.info("\n🔌  Conectando al servidor MCP de MetaTrader 5...")
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
    tools_response = await asyncio.wait_for(
      session.list_tools(),
      timeout=MCP_TOOL_TIMEOUT_SEC,
    )
    available_tools = tools_response.tools
    litellm_tools = mcp_tools_to_litellm(available_tools)

    log.info("✅  MCP conectado. Herramientas disponibles: %s\n", [t.name for t in available_tools])

    # ── Ejecución independiente por símbolo ────────────────
    symbol_decisions: list[dict] = []
    for symbol in SYMBOLS:
      log.info("\n📈  Iniciando análisis independiente para %s", symbol)
      symbol_memory_text = format_memory_for_symbol(memory, symbol)
      system_prompt = system_prompt_template.format(memory=symbol_memory_text)
      try:
        decision = await run_symbol_agent_loop(
          session=session,
          symbol=symbol,
          now_utc=now_utc,
          execution_windows=execution_windows,
          system_prompt=system_prompt,
          litellm_tools=litellm_tools,
          fixed_lot=fixed_lot,
        )
      except Exception as exc:
        log.error("❌  Error analizando %s: %s", symbol, exc, exc_info=True)
        continue

      if decision:
        symbol_decisions.append(decision)

  log.info("\n%s", "=" * 60)
  log.info("✅  Ejecución del agente completada.")
  if symbol_decisions:
    for decision in symbol_decisions:
      log.info(
        "    Decisión: %s | Símbolo: %s",
        decision.get("decision", "UNKNOWN"),
        decision.get("symbol", "N/A"),
      )
  else:
    log.info("    Sin decisiones registradas en esta corrida.")
  log.info("%s\n", "=" * 60)


# ──────────────────────────────────────────────────────────────
# ENTRY POINT
# ──────────────────────────────────────────────────────────────

if __name__ == "__main__":
  asyncio.run(run_agent())
