#!/usr/bin/env python3
"""
review_server.py — revisor interactivo de ofertas activas
===========================================================

Sirve en http://127.0.0.1:8765 la lista de ofertas con status='active' de
jobs.db (generado por job_radar.py) y deja marcar cada una como "ya postule"
o "no interesado" (con feedback opcional). La accion actualiza jobs.db al
vuelo y la oferta desaparece de la lista de activas.

Como correrlo:
  python3 review_server.py
"""

import html
import sqlite3
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs

HOST = "127.0.0.1"
PORT = 8765
DB_FILE = Path(__file__).parent / "jobs.db"

PAGE_HEAD = """<!doctype html>
<html lang="es">
<head>
<meta charset="utf-8">
<title>Ofertas activas</title>
<style>
  body { font-family: system-ui, sans-serif; max-width: 800px; margin: 2rem auto; padding: 0 1rem; }
  .job { border: 1px solid #ccc; border-radius: 8px; padding: 1rem; margin-bottom: 1rem; }
  .job h3 { margin: 0 0 .3rem; }
  .meta { color: #666; font-size: .9rem; margin-bottom: .5rem; }
  .razon { margin-bottom: .8rem; }
  form { display: inline-block; vertical-align: top; margin-right: .5rem; }
  textarea { display: block; width: 100%; box-sizing: border-box; margin: .4rem 0; font-family: inherit; }
  button { cursor: pointer; padding: .4rem .8rem; }
  .empty { color: #666; }
</style>
</head>
<body>
<h1>Ofertas activas</h1>
"""

PAGE_TAIL = "</body></html>"


def render_page() -> str:
    with sqlite3.connect(DB_FILE) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            "SELECT * FROM jobs WHERE status = 'active' ORDER BY score DESC"
        ).fetchall()

    if not rows:
        body = '<p class="empty">No hay ofertas activas pendientes de revisar.</p>'
    else:
        cards = []
        for r in rows:
            job_id = html.escape(r["job_id"])
            title = html.escape(r["title"] or "")
            company = html.escape(r["company"] or "")
            location = html.escape(r["location"] or "N/D")
            link = html.escape(r["link"] or "")
            razon = html.escape(r["razon"] or "")
            cards.append(f"""
<div class="job">
  <h3>{title} — {company} (score: {r['score']})</h3>
  <div class="meta">{location} · visto: {html.escape(r['first_seen'] or '')}</div>
  <div class="razon">{razon}</div>
  <p><a href="{link}" target="_blank" rel="noopener">Ver oferta</a></p>
  <form method="post" action="/action">
    <input type="hidden" name="job_id" value="{job_id}">
    <input type="hidden" name="action" value="applied">
    <button type="submit">Ya postulé</button>
  </form>
  <form method="post" action="/action">
    <input type="hidden" name="job_id" value="{job_id}">
    <input type="hidden" name="action" value="rejected">
    <textarea name="feedback" rows="2" placeholder="¿Por qué no te interesa? (opcional)"></textarea>
    <button type="submit">No interesado</button>
  </form>
</div>
""")
        body = "\n".join(cards)

    return PAGE_HEAD + body + PAGE_TAIL


class Handler(BaseHTTPRequestHandler):
    def log_message(self, format, *args):
        pass

    def do_GET(self):
        if self.path != "/":
            self.send_response(404)
            self.end_headers()
            return
        page = render_page().encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(page)))
        self.end_headers()
        self.wfile.write(page)

    def do_POST(self):
        if self.path != "/action":
            self.send_response(404)
            self.end_headers()
            return
        length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(length).decode("utf-8")
        fields = parse_qs(body)

        job_id = fields.get("job_id", [None])[0]
        action = fields.get("action", [None])[0]
        feedback = fields.get("feedback", [""])[0].strip()

        if job_id and action in ("applied", "rejected"):
            with sqlite3.connect(DB_FILE) as conn:
                conn.execute(
                    "UPDATE jobs SET status = ?, feedback = ?, decided_at = ? WHERE job_id = ?",
                    (action, feedback or None, datetime.now().strftime("%Y-%m-%d"), job_id),
                )

        self.send_response(303)
        self.send_header("Location", "/")
        self.end_headers()


def main():
    if not DB_FILE.exists():
        raise SystemExit("No existe jobs.db todavia. Corre job_radar.py primero.")
    server = ThreadingHTTPServer((HOST, PORT), Handler)
    print(f"Revisor de ofertas en http://{HOST}:{PORT}  (Ctrl+C para parar)")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
