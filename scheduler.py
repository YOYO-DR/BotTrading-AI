"""
Scheduler — lanza mt5_agent.py cada X minutos.

Uso:
    python scheduler.py               # cada 15 minutos (default)
    python scheduler.py --interval 5  # cada 5 minutos
    python scheduler.py --once        # ejecuta solo una vez y sale
"""

import argparse
import asyncio
import logging
import os
import signal
import sys
from datetime import datetime

from mt5_agent import run_agent

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)s  %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler("scheduler.log", encoding="utf-8"),
    ],
)
log = logging.getLogger("scheduler")

_running = True
_stop_signal_count = 0
_current_sleep_task: asyncio.Task | None = None


def _handle_stop(signum, frame):
    global _running, _stop_signal_count, _current_sleep_task
    _stop_signal_count += 1

    if _stop_signal_count == 1:
        log.info("Señal de parada recibida. Finalizando después de la iteración actual...")
    else:
        log.warning("Segunda señal recibida. Forzando salida inmediata.")
        os._exit(130)

    _running = False

    # Si está durmiendo entre iteraciones, cancelar sleep para salir de inmediato.
    if _current_sleep_task and not _current_sleep_task.done():
        _current_sleep_task.cancel()


signal.signal(signal.SIGINT,  _handle_stop)
signal.signal(signal.SIGTERM, _handle_stop)


async def scheduler_loop(interval_minutes: int, run_once: bool) -> None:
    global _running, _current_sleep_task
    iteration = 0

    while _running:
        iteration += 1
        log.info(f"━━━ Inicio de ejecución #{iteration}  [{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] ━━━")

        try:
            await run_agent()
        except Exception as exc:
            log.error(f"Error en el agente: {exc}", exc_info=True)

        if run_once:
            log.info("Modo --once: ejecución única completada.")
            break

        if _running:
            log.info(f"⏳  Próxima ejecución en {interval_minutes} minuto(s). "
                     f"Ctrl+C para detener.\n")
            try:
                _current_sleep_task = asyncio.create_task(asyncio.sleep(interval_minutes * 60))
                await _current_sleep_task
            except asyncio.CancelledError:
                break
            finally:
                _current_sleep_task = None

    log.info("Scheduler detenido.")


def main():
    parser = argparse.ArgumentParser(description="MT5 AI Agent Scheduler")
    parser.add_argument(
        "--interval", type=int, default=15,
        help="Intervalo entre ejecuciones en minutos (default: 15)"
    )
    parser.add_argument(
        "--once", action="store_true",
        help="Ejecutar el agente una sola vez y salir"
    )
    args = parser.parse_args()

    log.info(f"🚀  Scheduler iniciado | Intervalo: {args.interval} min | "
             f"Modo único: {args.once}")
    try:
        asyncio.run(scheduler_loop(args.interval, args.once))
    except KeyboardInterrupt:
        log.warning("Interrumpido por usuario.")


if __name__ == "__main__":
    main()
