# Bot Trader MT5 (CRT + MCP + OpenAI-compatible)

Bot de trading algorítmico que:

1. Se conecta a MetaTrader 5 a través de un servidor MCP (`metatrader-mcp-server`).
2. Usa un modelo LLM mediante API compatible con OpenAI (por ejemplo, LiteLLM Proxy).
3. Ejecuta una estrategia CRT (Candle Range Theory) definida en `strategy.md`.
4. Puede correr una vez o en modo scheduler cada X minutos.

## Estructura del proyecto

- `mt5_agent.py`: agente principal (conexión MCP, loop de tool-calls, decisión final, guardado de memoria).
- `scheduler.py`: ejecuta el agente en intervalos y maneja señales de parada (`Ctrl+C`).
- `mcp_precheck.py`: validación previa MCP -> MT5 (`initialize` + `list_tools`).
- `strategy.md`: prompt/sistema completo de la estrategia CRT.
- `CRT_TradingView_MCP_ClaudeCode.md`: documentación extensa de la estrategia y contexto.
- `requirements.txt`: dependencias Python.
- `env.example`: plantilla de variables de entorno.
- `.env`: variables reales locales (no compartir).
- `scheduler.log`: logs de ejecución del scheduler.

## Requisitos

- Windows (el proyecto está configurado para MT5 en Windows).
- Python 3.11+ (probado con 3.13).
- MetaTrader 5 instalado y logueado.
- `metatrader-mcp-server` disponible en el entorno (`PATH`) o configurado explícitamente.

## Instalación

```bash
python -m venv env
env\Scripts\activate
pip install -r requirements.txt
```

## Configuración (.env)

Usa `env.example` como base.

Variables principales:

- `MT5_LOGIN`: número de cuenta MT5.
- `MT5_PASSWORD`: contraseña de la cuenta.
- `MT5_SERVER`: servidor del broker MT5.
- `OPENAI_API_KEY`: API key para tu endpoint OpenAI-compatible.
- `BASE_URL`: endpoint base compatible con OpenAI, incluyendo `/v1`.
- `MODEL`: modelo a usar (ejemplo: `command-a-03-2025`, `qwen`, etc.).

Variables opcionales:

- `MCP_SERVER_COMMAND`: comando/ruta del servidor MCP (default `metatrader-mcp-server`).
- `MCP_CONNECT_TIMEOUT_SEC`: timeout de conexión MCP (default `30`).
- `LLM_REQUEST_TIMEOUT_SEC`: timeout de requests LLM (default `90`).
- `LLM_USER_AGENT`: user-agent para el cliente OpenAI SDK (default `curl/8.17.0`).

## Flujo recomendado de ejecución

### 1) Precheck MCP (recomendado)

```bash
env\Scripts\python.exe mcp_precheck.py --kill-stale --timeout 60
```

Si devuelve `[OK] MCP conectado correctamente`, puedes continuar.

### 2) Ejecutar una sola vez

```bash
python scheduler.py --once
```

### 3) Ejecutar cada 15 minutos

```bash
python scheduler.py --interval 15
```

## Modo directo (sin scheduler)

```bash
python mt5_agent.py
```

## Cómo funciona internamente

1. `mt5_agent.py` carga `.env` y `strategy.md`.
2. Conecta al servidor MCP de MT5 por `stdio`.
3. Obtiene herramientas MCP disponibles (`list_tools`).
4. Llama al LLM vía OpenAI SDK (`chat.completions.create`) con `tools`.
5. Ejecuta tool-calls MCP solicitadas por el modelo.
6. Repite hasta decisión final (`TRADE` o `NO_ENTRY`).
7. Si hay `TRADE`, guarda el resultado en `trade_memory.json`.

## Troubleshooting

### Error: `WinError 2` al conectar MCP

Causa típica: no se encuentra `metatrader-mcp-server`.

Acciones:

```bash
python -m pip install metatrader-mcp-server
```

O define `MCP_SERVER_COMMAND` en `.env` con ruta absoluta al ejecutable.

### Error: `Timeout conectando al MCP`

Causas típicas:

- MT5 no está abierto/logueado.
- hay procesos colgados de scheduler/agent/mcp.

Acciones:

```bash
env\Scripts\python.exe mcp_precheck.py --kill-stale --timeout 60
```

Y/o subir `MCP_CONNECT_TIMEOUT_SEC`.

### Error LLM `403 Your request was blocked`

Se observó que algunos endpoints filtran por `User-Agent`.

Acciones:

- Mantener `LLM_USER_AGENT=curl/8.17.0` (ya viene por defecto en el código).
- Verificar que `BASE_URL` sea correcto e incluya `/v1`.
- Verificar `OPENAI_API_KEY` válida para ese `BASE_URL`.

### `Ctrl+C` no detiene rápido el scheduler

`scheduler.py` ya maneja esto:

- primer `Ctrl+C`: parada limpia.
- segundo `Ctrl+C`: salida forzada.

## Notas de seguridad

- No subas `.env` al repositorio.
- Rota cualquier API key que haya sido expuesta en logs/chat.
- Evita imprimir claves en consola.

## Próximos pasos sugeridos

- Añadir tests de humo para `mcp_precheck.py` y parsing de tool-calls.
- Agregar `README` de despliegue para ejecutar como servicio en Windows.
- Versionar cambios en Git (`git init` ya está hecho).
