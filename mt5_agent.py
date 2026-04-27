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
REQUIRE_SL_ON_MARKET_ORDER = os.getenv("REQUIRE_SL_ON_MARKET_ORDER", "true").strip().lower() in (
  "1", "true", "yes", "on"
)
REQUIRE_TP_ON_ENTRY = os.getenv("REQUIRE_TP_ON_ENTRY", "true").strip().lower() in (
  "1", "true", "yes", "on"
)
EXECUTION_WINDOWS_UTC_RAW = os.getenv("EXECUTION_WINDOWS_UTC", "07:00-11:00,13:30-17:00")
EXECUTION_WINDOWS_COT_RAW = os.getenv("EXECUTION_WINDOWS_COT", "").strip()
EXECUTION_COT_UTC_OFFSET_HOURS = float(os.getenv("EXECUTION_COT_UTC_OFFSET_HOURS", "-5"))
ENFORCE_EXECUTION_WINDOWS = os.getenv("ENFORCE_EXECUTION_WINDOWS", "true").strip().lower() in (
  "1", "true", "yes", "on"
)
POSITION_REVIEW_BEFORE_NEW_ENTRY = os.getenv("POSITION_REVIEW_BEFORE_NEW_ENTRY", "true").strip().lower() in (
  "1", "true", "yes", "on"
)
BLOCK_ORDER_OUTSIDE_WINDOWS = os.getenv("BLOCK_ORDER_OUTSIDE_WINDOWS", "true").strip().lower() in (
  "1", "true", "yes", "on"
)

# pares CRT (Gold, NQ, Forex)
DEFAULT_SYMBOLS = ["EURUSD"]
SYMBOLS_RAW = os.getenv("SYMBOLS", "").strip()
SYMBOL_LOTS: dict[str, float] = {}
if SYMBOLS_RAW:
  SYMBOLS: list[str] = []
  for symbol_chunk in [chunk.strip() for chunk in SYMBOLS_RAW.split(",") if chunk.strip()]:
    symbol_name = symbol_chunk
    if ":" in symbol_chunk:
      symbol_name, lot_raw = symbol_chunk.rsplit(":", 1)
      symbol_name = symbol_name.strip()
      lot_raw = lot_raw.strip()

      if not symbol_name:
        raise RuntimeError(
          f"SYMBOLS inválido en segmento '{symbol_chunk}'. Debe tener símbolo antes de ':'."
        )
      try:
        symbol_lot = float(lot_raw)
      except ValueError as exc:
        raise RuntimeError(
          f"Lotaje inválido para {symbol_name} en SYMBOLS: '{lot_raw}'. Debe ser numérico."
        ) from exc
      if symbol_lot <= 0:
        raise RuntimeError(
          f"Lotaje inválido para {symbol_name} en SYMBOLS: {symbol_lot}. Debe ser > 0."
        )
      SYMBOL_LOTS[symbol_name.upper()] = symbol_lot
    else:
      symbol_name = symbol_name.strip()

    if symbol_name:
      SYMBOLS.append(symbol_name)

  if not SYMBOLS:
    raise RuntimeError(
      "SYMBOLS inválido: no se encontraron símbolos válidos. "
      "Formato esperado: SYMBOLS=EURUSD,GBPUSD,GOLD# o SYMBOLS=EURUSD:0.01,GBPUSD:0.02"
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
  trade_data.setdefault("trade_status", "OPEN")
  trade_data.setdefault("opened_at", datetime.now().isoformat())
  trade_data["saved_at"] = datetime.now().isoformat()
  memory.append(trade_data)
  save_memory(memory)
  log.info("\n💾  Operación guardada en %s", MEMORY_FILE)
  log.info(
    "    Ticket: %s | %s %s",
    trade_data.get("ticket", "N/A"),
    trade_data.get("symbol"),
    trade_data.get("direction"),
  )


def save_memory(memory: list[dict]) -> None:
  """Persiste en disco la memoria completa de operaciones."""
  with open(MEMORY_FILE, "w", encoding="utf-8") as f:
    json.dump(memory, f, indent=2, ensure_ascii=False)


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


def get_trade_lot_for_symbol(symbol: str, default_lot: float) -> float:
  """Obtiene lotaje por símbolo (SYMBOLS=SYM:LOT) o usa TRADE_LOT por defecto."""
  return SYMBOL_LOTS.get(symbol.upper(), default_lot)


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


def _collect_tickets_in_obj(obj: Any, tickets: set[int]) -> None:
  """Recolecta todos los tickets detectables en estructuras JSON."""
  if isinstance(obj, dict):
    for key in ("ticket", "order_ticket", "position_ticket", "deal_ticket"):
      ticket = _extract_positive_int(obj.get(key))
      if ticket:
        tickets.add(ticket)
    for value in obj.values():
      _collect_tickets_in_obj(value, tickets)
    return
  if isinstance(obj, list):
    for item in obj:
      _collect_tickets_in_obj(item, tickets)


def extract_tickets_from_tool_result(result_text: str) -> set[int]:
  """Extrae todos los tickets presentes en un resultado de tool (JSON o texto)."""
  tickets: set[int] = set()

  try:
    parsed = json.loads(result_text)
    _collect_tickets_in_obj(parsed, tickets)
  except Exception:
    pass

  for candidate in re.findall(r"\{[^{}]*\}", result_text):
    try:
      parsed = json.loads(candidate)
      _collect_tickets_in_obj(parsed, tickets)
    except Exception:
      continue

  for match in re.finditer(r"(?i)(?:ticket|position_ticket|order_ticket|deal_ticket)\D{0,10}(\d{3,})", result_text):
    ticket = _extract_positive_int(match.group(1))
    if ticket:
      tickets.add(ticket)

  return tickets


def infer_close_reason_from_deals(deals_text: str, ticket: int) -> str:
  """Intenta inferir si un ticket cerró por TP/SL usando texto de deals."""
  ticket_text = str(ticket)
  lower = deals_text.lower()
  index = lower.find(ticket_text.lower())
  if index == -1:
    return "CLOSED_UNKNOWN"

  start = max(0, index - 240)
  end = min(len(lower), index + 240)
  window = lower[start:end]

  if re.search(r"\b(tp|take\s*profit|take_profit)\b", window):
    return "CLOSED_TP"
  if re.search(r"\b(sl|stop\s*loss|stop_loss)\b", window):
    return "CLOSED_SL"
  return "CLOSED_UNKNOWN"


def get_numeric_arg(fn_args: dict[str, Any], keys: tuple[str, ...]) -> float | None:
  """Devuelve el primer valor numérico positivo para alguna clave candidata."""
  for key in keys:
    if key not in fn_args:
      continue
    raw_value = fn_args.get(key)
    if isinstance(raw_value, bool) or raw_value is None:
      continue
    try:
      numeric_value = float(raw_value)
    except (TypeError, ValueError):
      continue
    if numeric_value > 0:
      return numeric_value
  return None


def validate_market_order_risk_args(fn_args: dict[str, Any]) -> tuple[bool, bool, str | None]:
  """Valida que una entrada market tenga SL obligatorio y TP según política."""
  sl_value = get_numeric_arg(fn_args, ("sl", "stop_loss", "stopLoss", "stoploss"))
  tp_value = get_numeric_arg(fn_args, ("tp", "take_profit", "takeProfit", "takeprofit"))

  if REQUIRE_SL_ON_MARKET_ORDER and sl_value is None:
    return (
      False,
      False,
      "Bloqueado: place_market_order requiere SL obligatorio (sl/stop_loss) con valor > 0.",
    )

  if REQUIRE_TP_ON_ENTRY and tp_value is None:
    return (
      True,
      False,
      "Aviso: place_market_order sin TP. Debes agregar TP en la orden o con modify_position antes de finalizar TRADE.",
    )

  return True, tp_value is not None, None


async def get_open_positions_snapshot(
    session: ClientSession,
    symbol: str,
) -> tuple[str, set[int]]:
  """Consulta posiciones abiertas por símbolo y devuelve texto crudo + tickets detectados."""
  try:
    result = await asyncio.wait_for(
      session.call_tool("get_positions_by_symbol", arguments={"symbol": symbol}),
      timeout=MCP_TOOL_TIMEOUT_SEC,
    )
    result_text = " ".join(
      block.text if hasattr(block, "text") else str(block)
      for block in result.content
    )
    return result_text, extract_tickets_from_tool_result(result_text)
  except Exception as exc:
    error_text = f"ERROR precheck get_positions_by_symbol: {exc}"
    return error_text, set()


async def reconcile_symbol_trades_with_broker_state(
    session: ClientSession,
    symbol: str,
    open_tickets: set[int],
) -> list[str]:
  """Marca operaciones del símbolo como cerradas cuando ya no están abiertas en broker."""
  memory = load_memory()
  if not memory:
    return []

  tracked_open_entries: list[dict] = []
  for trade in memory:
    if str(trade.get("symbol", "")).upper() != symbol.upper():
      continue
    if str(trade.get("decision", "")).upper() != "TRADE":
      continue
    ticket = _extract_positive_int(trade.get("ticket"))
    if not ticket:
      continue
    status = str(trade.get("trade_status", "OPEN")).upper()
    if status.startswith("CLOSED"):
      continue
    tracked_open_entries.append(trade)

  if not tracked_open_entries:
    return []

  tracked_open_tickets = {
    _extract_positive_int(trade.get("ticket"))
    for trade in tracked_open_entries
    if _extract_positive_int(trade.get("ticket"))
  }
  closed_tickets = tracked_open_tickets - open_tickets
  if not closed_tickets:
    return []

  deals_text = ""
  try:
    deals_result = await asyncio.wait_for(
      session.call_tool("get_deals", arguments={}),
      timeout=MCP_TOOL_TIMEOUT_SEC,
    )
    deals_text = " ".join(
      block.text if hasattr(block, "text") else str(block)
      for block in deals_result.content
    )
  except Exception as exc:
    log.warning(
      "⚠️  [%s] No se pudo consultar get_deals para inferir motivo de cierre: %s",
      symbol,
      exc,
    )

  now_iso = datetime.now().isoformat()
  updates: list[str] = []
  for trade in tracked_open_entries:
    ticket = _extract_positive_int(trade.get("ticket"))
    if not ticket or ticket not in closed_tickets:
      continue

    close_reason = infer_close_reason_from_deals(deals_text, ticket) if deals_text else "CLOSED_UNKNOWN"
    trade["trade_status"] = close_reason
    trade["closed_at"] = now_iso
    updates.append(f"Ticket {ticket} -> {close_reason}")

  if updates:
    save_memory(memory)

  return updates


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
      "1. Si hay posiciones abiertas del símbolo, primero haz gestión de posiciones: "
      "evalúa si se dejan abiertas o se cierran con close_position/close_all_positions_by_symbol. "
      "No busques nueva entrada hasta completar esta revisión.\n"
      f"2. Para este activo obtén velas de {', '.join(TIMEFRAMES)}.\n"
      "3. Determina el Daily Bias en D1 (alcista / bajista / ambiguo).\n"
      "4. Si el bias está definido, identifica el setup CRT en H4 (velas 1am/5am EST de referencia).\n"
      "5. Baja a M15 para confirmar la Vela 3 (cierre dentro del rango) y buscar confluencias "
      "(Order Block, FVG, Killzone).\n"
      "6. Si todos los filtros pasan, ejecuta SI O SI la operación con place_market_order "
      "(obligatorio). No simules ejecución, no la dejes pendiente y no cierres análisis sin llamar la tool.\n"
      f"7. LOTAJE FIJO OBLIGATORIO: usa exactamente {fixed_lot} lotes. "
      "No propongas ni uses otro volumen.\n"
      "8. En place_market_order el Stop Loss (SL) es obligatorio. "
      "Si la tool permite TP en la entrada, inclúyelo de una vez; si no, agrega TP inmediatamente con modify_position.\n"
      "9. SOLO devuelve decision=TRADE si ejecutaste place_market_order en esta corrida y tienes ticket real (>0). "
      "Si no ejecutaste la tool o no hay ticket válido, devuelve NO_ENTRY.\n"
      "10. Si estás fuera de las ventanas operativas UTC, NO debes llamar place_market_order. "
      "En ese caso finaliza con NO_ENTRY sin ejecutar órdenes.\n"
      "11. Está prohibido inventar o estimar tickets. El ticket debe venir de la respuesta real de la tool.\n"
      "12. Si detectas posiciones previamente abiertas, reporta en la decisión el estado de seguimiento: "
      "dejar abierta / cerrar / cerrada por SL / cerrada por TP (si la data del broker lo permite).\n"
      "13. No mezcles análisis ni velas con otros símbolos. Este contexto es exclusivo de este activo.\n"
      "14. Responde con el bloque JSON de decisión."
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

  if POSITION_REVIEW_BEFORE_NEW_ENTRY:
    open_positions_snapshot, open_tickets = await get_open_positions_snapshot(session, symbol)
    reconciliation_updates = await reconcile_symbol_trades_with_broker_state(
      session=session,
      symbol=symbol,
      open_tickets=open_tickets,
    )

    precheck_message = (
      f"PRECHECK POSICIONES ABIERTAS ({symbol}): {len(open_tickets)} detectadas.\n"
      f"Snapshot broker (resumen): {open_positions_snapshot[:1800]}\n"
    )
    if reconciliation_updates:
      precheck_message += "Actualizaciones de seguimiento en memoria: " + "; ".join(reconciliation_updates)
    else:
      precheck_message += "Actualizaciones de seguimiento en memoria: ninguna."
  else:
    precheck_message = (
      "PRECHECK POSICIONES ABIERTAS desactivado por configuración "
      "(POSITION_REVIEW_BEFORE_NEW_ENTRY=false)."
    )

  inside_execution_window = is_time_in_windows_utc(now_utc.time(), execution_windows)

  messages: list[dict[str, Any]] = [{"role": "user", "content": f"{user_message}\n\n{precheck_message}"}]
  iteration = 0
  final_decision = None
  executed_order_ticket: int | None = None
  attempted_order_placement = False
  attempted_market_order = False
  market_order_without_tp = False
  tp_added_after_entry = False
  last_market_order_type: str | None = None

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
        if executed_order_ticket and str(final_decision.get("decision", "")).upper() != "TRADE":
          previous_decision = final_decision.get("decision")
          log.warning(
            "⚠️  [%s] El modelo devolvió %s pero existe ticket real ejecutado (%s). Se corrige a TRADE.",
            symbol,
            previous_decision,
            executed_order_ticket,
          )
          final_decision["decision"] = "TRADE"
          final_decision["ticket"] = executed_order_ticket
          if not final_decision.get("direction") and last_market_order_type:
            final_decision["direction"] = last_market_order_type
          existing_reason = str(final_decision.get("reason", "")).strip()
          correction_reason = (
            f"Ajuste automático: se ejecutó orden real con ticket {executed_order_ticket}; "
            "no se permite reportar NO_ENTRY cuando hay ejecución real."
          )
          final_decision["reason"] = (
            f"{existing_reason} | {correction_reason}" if existing_reason else correction_reason
          )

        decision_type = final_decision.get("decision", "UNKNOWN")
        log.info("🏁  [%s] DECISIÓN: %s", symbol, decision_type)

        if decision_type == "TRADE":
          if not attempted_market_order:
            log.warning(
              "⚠️  [%s] El modelo devolvió TRADE sin ejecutar place_market_order. Se fuerza NO_ENTRY.",
              symbol,
            )
            final_decision["decision"] = "NO_ENTRY"
            final_decision["ticket"] = None
            final_decision["reason"] = (
              "Entrada detectada pero no se ejecutó place_market_order real; "
              "se invalida TRADE para evitar ticket ficticio."
            )
            log.info("⛔  [%s] Sin entrada. Razón: %s", symbol, final_decision.get("reason", "N/A"))
            return final_decision

          if REQUIRE_TP_ON_ENTRY and market_order_without_tp and not tp_added_after_entry:
            log.warning(
              "⚠️  [%s] TRADE inválido: se ejecutó entrada sin TP y no se agregó TP luego con modify_position.",
              symbol,
            )
            final_decision["decision"] = "NO_ENTRY"
            final_decision["ticket"] = None
            final_decision["reason"] = (
              "Entrada ejecutada sin TP. Debe incluir TP en place_market_order o agregarlo con modify_position."
            )
            log.info("⛔  [%s] Sin entrada. Razón: %s", symbol, final_decision.get("reason", "N/A"))
            return final_decision

          model_ticket = _extract_positive_int(final_decision.get("ticket"))
          resolved_ticket = executed_order_ticket or model_ticket

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
            "    [%s] ℹ️ Lote forzado por configuración del símbolo (%s lotes).",
            symbol,
            fixed_lot,
          )

      log.info("🔧  [%s] Llamando herramienta: %s", symbol, fn_name)
      log.info("    [%s] Args: %s", symbol, json.dumps(fn_args, ensure_ascii=False)[:200])

      if fn_name == "place_market_order":
        if BLOCK_ORDER_OUTSIDE_WINDOWS and not inside_execution_window:
          blocked_msg = (
            "Bloqueado: fuera de ventana operativa UTC. "
            "No se permite ejecutar place_market_order en este horario."
          )
          log.info("    [%s] %s", symbol, blocked_msg)
          tool_results.append({
            "role": "tool",
            "tool_call_id": tool_call["id"],
            "content": f"ERROR validación horario: {blocked_msg}",
          })
          continue

        is_valid_risk, has_tp_in_order, risk_msg = validate_market_order_risk_args(fn_args)
        if risk_msg:
          log.info("    [%s] %s", symbol, risk_msg)
        if not is_valid_risk:
          tool_results.append({
            "role": "tool",
            "tool_call_id": tool_call["id"],
            "content": f"ERROR validación riesgo: {risk_msg}",
          })
          continue
        if REQUIRE_TP_ON_ENTRY and not has_tp_in_order:
          market_order_without_tp = True

        order_type = fn_args.get("type") if isinstance(fn_args, dict) else None
        if isinstance(order_type, str):
          last_market_order_type = order_type.upper()

      if fn_name == "modify_position":
        tp_after = get_numeric_arg(fn_args if isinstance(fn_args, dict) else {}, ("tp", "take_profit", "takeProfit", "takeprofit"))
        if tp_after is not None:
          tp_added_after_entry = tp_added_after_entry or attempted_market_order

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
          if fn_name == "place_market_order":
            attempted_market_order = True
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
  default_lot = get_trade_lot()
  symbol_lots_preview = [
    f"{symbol}:{get_trade_lot_for_symbol(symbol, default_lot)}"
    for symbol in SYMBOLS
  ]
  log.info("    Lotaje por símbolo: %s", ", ".join(symbol_lots_preview))
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
      symbol_lot = get_trade_lot_for_symbol(symbol, default_lot)
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
          fixed_lot=symbol_lot,
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
