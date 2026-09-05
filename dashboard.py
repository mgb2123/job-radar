#!/usr/bin/env python3
"""
dashboard.py — panel unico para manejar job_radar
====================================================

Sirve en http://127.0.0.1:8765 todo lo necesario para no tener que tocar
codigo ni cron a mano dia a dia:

  - Activas      — revisar ofertas nuevas y marcarlas aplicada/rechazada
                    (reemplaza a review_server.py).
  - Lanzar busqueda — correr job_radar.py bajo demanda, con el log en vivo.
  - Historial    — ofertas ya decididas, con su feedback.
  - Estadisticas — contadores, distribucion de scores, ultima ejecucion.
  - Configuracion — editar CV/preferencias y parametros de busqueda sin
                    tocar codigo (persiste en config.json).

Como correrlo:
  pip install -r requirements.txt --break-system-packages
  python3 dashboard.py
"""

import os
import sqlite3
import sys
import threading
import time
from collections import Counter
from datetime import datetime
from pathlib import Path

from flask import Flask, Response, redirect, render_template, request, url_for

from config import load_config, save_config
from job_radar import DB_FILE, init_db

HOST = "127.0.0.1"
PORT = 8765
BASE_DIR = Path(__file__).parent
JOB_RADAR_PATH = BASE_DIR / "job_radar.py"

app = Flask(__name__)


def get_db():
    conn = sqlite3.connect(DB_FILE)
    conn.row_factory = sqlite3.Row
    return conn


# ---------------------------------------------------------------------------
# Activas — paridad con review_server.py
# ---------------------------------------------------------------------------

@app.route("/")
def active():
    with get_db() as conn:
        jobs = conn.execute(
            "SELECT * FROM jobs WHERE status = 'active' ORDER BY score DESC"
        ).fetchall()
    return render_template("active.html", jobs=jobs, active_tab="active")


@app.route("/action", methods=["POST"])
def action():
    job_id = request.form.get("job_id")
    act = request.form.get("action")
    feedback = (request.form.get("feedback") or "").strip()

    if job_id and act in ("applied", "rejected"):
        with get_db() as conn:
            conn.execute(
                "UPDATE jobs SET status = ?, feedback = ?, decided_at = ? WHERE job_id = ?",
                (act, feedback or None, datetime.now().strftime("%Y-%m-%d"), job_id),
            )
    return redirect(url_for("active"))


# ---------------------------------------------------------------------------
# Historial
# ---------------------------------------------------------------------------

@app.route("/history")
def history():
    with get_db() as conn:
        jobs = conn.execute(
            "SELECT * FROM jobs WHERE status IN ('applied', 'rejected') ORDER BY decided_at DESC"
        ).fetchall()
    return render_template("history.html", jobs=jobs, active_tab="history")


# ---------------------------------------------------------------------------
# Estadisticas
# ---------------------------------------------------------------------------

@app.route("/stats")
def stats():
    with get_db() as conn:
        rows = conn.execute("SELECT status, COUNT(*) AS n FROM jobs GROUP BY status").fetchall()
        counts = {r["status"]: r["n"] for r in rows}

        scores = [r[0] for r in conn.execute("SELECT score FROM jobs WHERE score IS NOT NULL")]
        bucket_counts = Counter((s // 10) * 10 for s in scores)
        buckets = sorted(bucket_counts.items())
        max_count = max(bucket_counts.values()) if bucket_counts else 1

        last_run = conn.execute("SELECT * FROM runs ORDER BY id DESC LIMIT 1").fetchone()
        recent_runs = conn.execute("SELECT * FROM runs ORDER BY id DESC LIMIT 10").fetchall()
        last_seen = conn.execute("SELECT MAX(first_seen) FROM jobs").fetchone()[0]

    return render_template(
        "stats.html",
        counts=counts,
        buckets=buckets,
        max_count=max_count,
        last_run=last_run,
        recent_runs=recent_runs,
        last_seen=last_seen,
        active_tab="stats",
    )


# ---------------------------------------------------------------------------
# Configuracion
# ---------------------------------------------------------------------------

def _validate_config(form) -> tuple[dict | None, list[str]]:
    errors = []

    queries = [q.strip() for q in form.get("search_queries", "").splitlines() if q.strip()]
    if not queries:
        errors.append("Debe haber al menos una búsqueda.")

    countries = [c.strip().lower() for c in form.get("search_countries", "").splitlines() if c.strip()]
    if not countries:
        errors.append("Debe haber al menos un país.")

    try:
        results_per_query = int(form.get("results_per_query", ""))
        if not (1 <= results_per_query <= 100):
            raise ValueError
    except ValueError:
        errors.append("Resultados por búsqueda debe ser un entero entre 1 y 100.")
        results_per_query = None

    model = form.get("openrouter_model", "").strip()
    if not model:
        errors.append("El modelo de OpenRouter no puede estar vacío.")

    try:
        score_threshold = int(form.get("score_threshold", ""))
        if not (0 <= score_threshold <= 100):
            raise ValueError
    except ValueError:
        errors.append("El umbral de score debe ser un entero entre 0 y 100.")
        score_threshold = None

    cv_context = form.get("cv_context", "").strip()
    if not cv_context:
        errors.append("El CV/preferencias no puede estar vacío.")

    if errors:
        return None, errors

    return {
        "search_queries": queries,
        "search_countries": countries,
        "results_per_query": results_per_query,
        "openrouter_model": model,
        "score_threshold": score_threshold,
        "cv_context": cv_context,
    }, []


@app.route("/config", methods=["GET", "POST"])
def config_edit():
    if request.method == "POST":
        cfg, errors = _validate_config(request.form)
        if errors:
            # re-renderiza con lo que el usuario tecleo, sin perder nada
            raw_cfg = {
                "search_queries": [q.strip() for q in request.form.get("search_queries", "").splitlines() if q.strip()],
                "search_countries": [c.strip() for c in request.form.get("search_countries", "").splitlines() if c.strip()],
                "results_per_query": request.form.get("results_per_query", ""),
                "openrouter_model": request.form.get("openrouter_model", ""),
                "score_threshold": request.form.get("score_threshold", ""),
                "cv_context": request.form.get("cv_context", ""),
            }
            return render_template("config_edit.html", cfg=raw_cfg, errors=errors, saved=False, active_tab="config")
        save_config(cfg)
        return redirect(url_for("config_edit", saved=1))

    cfg = load_config()
    saved = request.args.get("saved") == "1"
    return render_template("config_edit.html", cfg=cfg, errors=[], saved=saved, active_tab="config")


# ---------------------------------------------------------------------------
# Lanzar busqueda + streaming de log
# ---------------------------------------------------------------------------

_run_lock = threading.Lock()
_run_state = {"process": None, "running": False, "log_lines": []}


def _reader_thread(proc):
    for line in proc.stdout:
        with _run_lock:
            _run_state["log_lines"].append(line.rstrip("\n"))
    proc.wait()
    with _run_lock:
        _run_state["running"] = False
        _run_state["process"] = None


@app.route("/run")
def run_page():
    with _run_lock:
        running = _run_state["running"]
        log_lines = list(_run_state["log_lines"])
    return render_template("run.html", running=running, log_lines=log_lines, active_tab="run")


@app.route("/run/start", methods=["POST"])
def run_start():
    import subprocess

    with _run_lock:
        if _run_state["running"]:
            return redirect(url_for("run_page"))
        cmd = [sys.executable, str(JOB_RADAR_PATH)]
        proc = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
            cwd=BASE_DIR,
        )
        _run_state["process"] = proc
        _run_state["running"] = True
        _run_state["log_lines"] = []
        threading.Thread(target=_reader_thread, args=(proc,), daemon=True).start()
    return redirect(url_for("run_page"))


@app.route("/run/stream")
def run_stream():
    since = int(request.args.get("since", 0))

    def gen():
        idx = since
        while True:
            with _run_lock:
                new_lines = _run_state["log_lines"][idx:]
                running = _run_state["running"]
            for line in new_lines:
                yield f"data: {line}\n\n"
            idx += len(new_lines)
            if not running and not new_lines:
                yield "event: done\ndata: end\n\n"
                break
            time.sleep(0.3)

    return Response(
        gen(),
        mimetype="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


def main():
    init_db()  # idempotente — crea jobs.db/tabla runs si no existen todavia
    print(f"Dashboard en http://{HOST}:{PORT}  (Ctrl+C para parar)")
    app.run(host=HOST, port=PORT)


if __name__ == "__main__":
    main()
