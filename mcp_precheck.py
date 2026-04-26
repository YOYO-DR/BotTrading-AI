r"""
Pre-check MCP -> MetaTrader 5.

Valida que el servidor MCP arranque, responda initialize() y liste herramientas,
antes de ejecutar scheduler.py.

Uso:
    env\Scripts\python.exe mcp_precheck.py
    python mcp_precheck.py --timeout 40
    python mcp_precheck.py --kill-stale
"""

import argparse
import asyncio
import os
import shutil
import subprocess
import sys
from contextlib import AsyncExitStack

from dotenv import load_dotenv
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client


def _iter_leaf_exceptions(exc: BaseException):
    """Recorre recursivamente ExceptionGroup y devuelve excepciones hoja."""
    if isinstance(exc, BaseExceptionGroup):
        for sub_exc in exc.exceptions:
            yield from _iter_leaf_exceptions(sub_exc)
    else:
        yield exc


def _format_exception_group(exc: BaseExceptionGroup) -> str:
    """Construye un resumen legible de causas dentro de ExceptionGroup."""
    lines: list[str] = []
    for leaf in _iter_leaf_exceptions(exc):
        # CancelledError suele ser efecto colateral del timeout/cierre.
        if isinstance(leaf, asyncio.CancelledError):
            continue
        lines.append(f"- {type(leaf).__name__}: {leaf}")

    if not lines:
        return "- No se pudo extraer una causa específica (todas fueron cancelaciones)."
    return "\n".join(lines)


def kill_stale_processes() -> None:
    """Mata procesos colgados de scheduler/agent/mcp en Windows."""
    commands = [
        ["taskkill", "/IM", "metatrader-mcp-server.exe", "/F"],
        ["powershell", "-NoProfile", "-Command",
         "Get-CimInstance Win32_Process | "
         "Where-Object { $_.Name -eq 'python.exe' -and $_.CommandLine -match 'scheduler.py|mt5_agent.py' } | "
         "ForEach-Object { Stop-Process -Id $_.ProcessId -Force }"]
    ]

    for cmd in commands:
        try:
            subprocess.run(cmd, check=False, capture_output=True, text=True)
        except Exception as exc:
            print(f"[WARN] No se pudo ejecutar limpieza: {exc}")


async def run_check(timeout: float) -> None:
    load_dotenv()

    mcp_command = os.getenv("MCP_SERVER_COMMAND", "metatrader-mcp-server")
    mt5_login = os.getenv("MT5_LOGIN", "")
    mt5_password = os.getenv("MT5_PASSWORD", "")
    mt5_server = os.getenv("MT5_SERVER", "")

    missing = [
        name for name, value in (
            ("MT5_LOGIN", mt5_login),
            ("MT5_PASSWORD", mt5_password),
            ("MT5_SERVER", mt5_server),
        ) if not value
    ]
    if missing:
        raise RuntimeError(f"Faltan variables en .env: {', '.join(missing)}")

    if shutil.which(mcp_command) is None:
        raise RuntimeError(
            f"No se encontró el ejecutable MCP '{mcp_command}' en PATH. "
            "Instálalo o define MCP_SERVER_COMMAND con una ruta válida."
        )

    server_params = StdioServerParameters(
        command=mcp_command,
        args=[
            "--login", mt5_login,
            "--password", mt5_password,
            "--server", mt5_server,
            "--transport", "stdio",
            "--path", r"C:\Program Files\XM Global MT5\terminal64.exe",
        ],
        env=None,
    )

    print("=" * 60)
    print("MCP PRECHECK - MetaTrader 5")
    print(f"Command : {mcp_command}")
    print(f"Server  : {mt5_server}")
    print(f"Timeout : {timeout}s")
    print("=" * 60)
    print("\nConectando al servidor MCP...")

    async with AsyncExitStack() as stack:
        read, write = await asyncio.wait_for(
            stack.enter_async_context(stdio_client(server_params)),
            timeout=timeout,
        )
        session: ClientSession = await asyncio.wait_for(
            stack.enter_async_context(ClientSession(read, write)),
            timeout=timeout,
        )
        await asyncio.wait_for(session.initialize(), timeout=timeout)
        tools_response = await asyncio.wait_for(session.list_tools(), timeout=timeout)

    tools = [tool.name for tool in tools_response.tools]
    print("\n[OK] MCP conectado correctamente.")
    print(f"[OK] Herramientas disponibles ({len(tools)}): {tools}")


def main() -> int:
    parser = argparse.ArgumentParser(description="Pre-check MCP -> MT5")
    parser.add_argument("--timeout", type=float, default=30.0, help="Timeout en segundos (default: 30)")
    parser.add_argument("--kill-stale", action="store_true", help="Mata procesos colgados antes del check")
    args = parser.parse_args()

    try:
        if args.kill_stale:
            print("Limpiando procesos colgados...")
            kill_stale_processes()

        asyncio.run(run_check(args.timeout))
        return 0
    except TimeoutError:
        print("\n[ERROR] Timeout conectando al MCP. Revisa MT5 abierto/logueado y aumenta --timeout.")
        return 2
    except BaseExceptionGroup as exc:
        print("\n[ERROR] Fallo en handshake MCP (ExceptionGroup). Causas detectadas:")
        print(_format_exception_group(exc))
        return 3
    except Exception as exc:
        print(f"\n[ERROR] {exc}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
