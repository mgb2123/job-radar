#!/usr/bin/env python3
"""
gui.py — abre el dashboard como una app de escritorio (sin terminal)
=======================================================================

Arranca el mismo servidor Flask de dashboard.py en segundo plano y lo
muestra en una ventana nativa (pywebview) en vez de en una pestana de
navegador: doble-click y listo, sin exportar variables de entorno ni
recordar una URL/puerto (las claves de API se gestionan desde la pestana
"Claves API", ver secrets_store.py).

Como correrlo:
  pip install -r requirements.txt --break-system-packages
  python3 gui.py

Tambien puedes copiar job-radar.desktop a ~/.local/share/applications/
para tener un icono de app normal en tu escritorio/menu.
"""

import socket
import threading
import time

import webview

import dashboard


def _wait_for_server(host: str, port: int, timeout: float = 5.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            with socket.create_connection((host, port), timeout=0.2):
                return
        except OSError:
            time.sleep(0.1)


def _run_flask() -> None:
    dashboard.init_db()
    dashboard.app.run(host=dashboard.HOST, port=dashboard.PORT, debug=False, use_reloader=False)


def main() -> None:
    threading.Thread(target=_run_flask, daemon=True).start()
    _wait_for_server(dashboard.HOST, dashboard.PORT)
    webview.create_window(
        "Job Radar",
        f"http://{dashboard.HOST}:{dashboard.PORT}",
        width=1360,
        height=880,
        min_size=(900, 600),
    )
    webview.start()


if __name__ == "__main__":
    main()
