#!/usr/bin/env python3
"""
dashboard.py — panel unico para manejar job_radar
====================================================

Sirve en http://127.0.0.1:8765 todo lo necesario para no tener que tocar
codigo ni cron a mano dia a dia:

  - Activas      — revisar ofertas nuevas y marcarlas aplicada/rechazada
                    (reemplaza a review_server.py).
  - Lanzar busqueda — correr job_radar.py bajo demanda, con filtros propios
                    (queries/paises solo para esa corrida) y el log en vivo;
                    al terminar, muestra unicamente los resultados de esa
                    busqueda.
  - Historial    — ofertas ya decididas, con su feedback.
  - Estadisticas — contadores, distribucion de scores, ultima ejecucion.
  - Configuracion — editar CV/preferencias y parametros de busqueda sin
                    tocar codigo (persiste en config.json).
  - Listas       — etiquetas para organizar ofertas (una oferta puede estar
                    en varias listas a la vez), independientes de su status.

Como correrlo:
  pip install -r requirements.txt --break-system-packages
  python3 dashboard.py
"""

import json
import os
import re
import sqlite3
import subprocess
import sys
import threading
import time
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path

from flask import Flask, Response, abort, redirect, render_template, request, url_for

from config import NIVELES_MCER, load_config, nivel_index, save_config
from job_radar import DB_FILE, init_db
from pricing import price_per_million
from secrets_store import load_secrets, save_secrets

HOST = "127.0.0.1"
PORT = 8765
BASE_DIR = Path(__file__).parent
JOB_RADAR_PATH = BASE_DIR / "job_radar.py"

app = Flask(__name__)


@app.template_filter("flag_emoji")
def flag_emoji(country_code):
    if not country_code or len(country_code) != 2 or not country_code.isalpha():
        return ""
    return "".join(chr(0x1F1E6 + ord(c) - ord("A")) for c in country_code.upper())


def get_db():
    conn = sqlite3.connect(DB_FILE)
    conn.row_factory = sqlite3.Row
    return conn


def attach_lists(conn, jobs: list[dict]) -> None:
    """Anade una clave 'lists' (lista de {id, name}) a cada job in-place."""
    job_ids = [j["job_id"] for j in jobs]
    by_job = defaultdict(list)
    if job_ids:
        placeholders = ",".join("?" * len(job_ids))
        rows = conn.execute(
            f"""
            SELECT jl.job_id, l.id, l.name FROM job_lists jl
            JOIN lists l ON l.id = jl.list_id
            WHERE jl.job_id IN ({placeholders})
            ORDER BY l.name
            """,
            job_ids,
        ).fetchall()
        for r in rows:
            by_job[r["job_id"]].append({"id": r["id"], "name": r["name"]})
    for j in jobs:
        j["lists"] = by_job.get(j["job_id"], [])


def all_lists(conn):
    return conn.execute("SELECT * FROM lists ORDER BY name").fetchall()


def user_languages(cfg: dict) -> dict[str, str]:
    """{idioma: nivel} del usuario, uniendo los idiomas fijos y los 'otros'."""
    langs = {k: v for k, v in (cfg.get("idiomas_usuario") or {}).items() if v}
    for entry in cfg.get("idiomas_usuario_otros") or []:
        idioma = str(entry.get("idioma", "")).strip().lower()
        nivel = str(entry.get("nivel", "")).strip().upper()
        if idioma and nivel in NIVELES_MCER:
            langs[idioma] = nivel
    return langs


def attach_language_flags(jobs: list[dict], user_langs: dict[str, str]) -> None:
    """Anade 'idiomas_check' (lista de {idioma, nivel_requerido, nivel_usuario, cumple})
    a cada job, comparando lo que pide la oferta con el nivel del usuario.
    cumple es True/False, o None si el usuario no indico nivel para ese idioma."""
    for j in jobs:
        raw = j.get("idiomas_requeridos")
        try:
            required = json.loads(raw) if raw else []
        except (json.JSONDecodeError, TypeError):
            required = []
        checks = []
        for item in required:
            if not isinstance(item, dict):
                continue
            idioma = str(item.get("idioma", "")).strip().lower()
            nivel_req = str(item.get("nivel", "")).strip().upper()
            if not idioma or nivel_req not in NIVELES_MCER:
                continue
            nivel_usuario = user_langs.get(idioma)
            cumple = nivel_index(nivel_usuario) >= nivel_index(nivel_req) if nivel_usuario else None
            checks.append({
                "idioma": idioma,
                "nivel_requerido": nivel_req,
                "nivel_usuario": nivel_usuario,
                "cumple": cumple,
            })
        j["idiomas_check"] = checks


# ---------------------------------------------------------------------------
# Activas — paridad con review_server.py
# ---------------------------------------------------------------------------

@app.route("/")
def active():
    hide_lang_mismatch = request.args.get("hide_lang_mismatch") == "1"
    country_filter = request.args.get("country", "").strip().upper()
    list_filter = request.args.get("list_id", "").strip()

    with get_db() as conn:
        jobs = [dict(r) for r in conn.execute(
            "SELECT * FROM jobs WHERE status = 'active' ORDER BY score DESC"
        ).fetchall()]
        attach_lists(conn, jobs)
        attach_language_flags(jobs, user_languages(load_config()))
        lists = all_lists(conn)
        available_countries = sorted({j["country"] for j in jobs if j["country"]})

    if hide_lang_mismatch:
        jobs = [j for j in jobs if not any(c["cumple"] is False for c in j["idiomas_check"])]
    if country_filter:
        jobs = [j for j in jobs if j["country"] == country_filter]
    if list_filter:
        jobs = [j for j in jobs if any(str(l["id"]) == list_filter for l in j["lists"])]

    return render_template(
        "active.html",
        jobs=jobs,
        all_lists=lists,
        available_countries=available_countries,
        hide_lang_mismatch=hide_lang_mismatch,
        country_filter=country_filter,
        list_filter=list_filter,
        active_tab="active",
    )


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
# Listas — etiquetas para organizar ofertas (independientes de status)
# ---------------------------------------------------------------------------

@app.route("/lists")
def lists_page():
    with get_db() as conn:
        lists = conn.execute(
            """
            SELECT l.id, l.name, l.created_at, COUNT(jl.job_id) AS job_count
            FROM lists l LEFT JOIN job_lists jl ON jl.list_id = l.id
            GROUP BY l.id ORDER BY l.name
            """
        ).fetchall()
    return render_template("lists.html", lists=lists, active_tab="lists")


@app.route("/lists/create", methods=["POST"])
def lists_create():
    name = request.form.get("name", "").strip()
    if name:
        with get_db() as conn:
            conn.execute(
                "INSERT OR IGNORE INTO lists (name, created_at) VALUES (?, ?)",
                (name, datetime.now().isoformat(timespec="seconds")),
            )
    return redirect(url_for("lists_page"))


@app.route("/lists/<int:list_id>")
def list_detail(list_id):
    with get_db() as conn:
        list_row = conn.execute("SELECT * FROM lists WHERE id = ?", (list_id,)).fetchone()
        if list_row is None:
            abort(404)
        jobs = [dict(r) for r in conn.execute(
            """
            SELECT j.* FROM jobs j
            JOIN job_lists jl ON jl.job_id = j.job_id
            WHERE jl.list_id = ? ORDER BY jl.added_at DESC
            """,
            (list_id,),
        ).fetchall()]
        attach_lists(conn, jobs)
        attach_language_flags(jobs, user_languages(load_config()))
        lists = all_lists(conn)
    return render_template("list_detail.html", the_list=list_row, jobs=jobs, all_lists=lists, active_tab="lists")


@app.route("/lists/<int:list_id>/delete", methods=["POST"])
def lists_delete(list_id):
    with get_db() as conn:
        conn.execute("DELETE FROM job_lists WHERE list_id = ?", (list_id,))
        conn.execute("DELETE FROM lists WHERE id = ?", (list_id,))
    return redirect(url_for("lists_page"))


@app.route("/joblists/add", methods=["POST"])
def job_lists_add():
    job_id = request.form.get("job_id")
    name = (request.form.get("new_list_name") or request.form.get("list_name") or "").strip()
    next_url = request.form.get("next") or url_for("active")
    if job_id and name:
        with get_db() as conn:
            conn.execute(
                "INSERT OR IGNORE INTO lists (name, created_at) VALUES (?, ?)",
                (name, datetime.now().isoformat(timespec="seconds")),
            )
            list_id = conn.execute("SELECT id FROM lists WHERE name = ?", (name,)).fetchone()["id"]
            conn.execute(
                "INSERT OR IGNORE INTO job_lists (job_id, list_id, added_at) VALUES (?, ?, ?)",
                (job_id, list_id, datetime.now().isoformat(timespec="seconds")),
            )
    return redirect(next_url)


@app.route("/joblists/remove", methods=["POST"])
def job_lists_remove():
    job_id = request.form.get("job_id")
    list_id = request.form.get("list_id")
    next_url = request.form.get("next") or url_for("active")
    if job_id and list_id:
        with get_db() as conn:
            conn.execute("DELETE FROM job_lists WHERE job_id = ? AND list_id = ?", (job_id, list_id))
    return redirect(next_url)


# ---------------------------------------------------------------------------
# Historial
# ---------------------------------------------------------------------------

@app.route("/history")
def history():
    with get_db() as conn:
        jobs = [dict(r) for r in conn.execute(
            "SELECT * FROM jobs WHERE status IN ('applied', 'rejected') ORDER BY decided_at DESC"
        ).fetchall()]
        attach_lists(conn, jobs)
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
# Coste — estimacion aproximada a partir de los tokens de cada llamada al LLM
# ---------------------------------------------------------------------------

@app.route("/costs")
def costs():
    with get_db() as conn:
        calls = conn.execute(
            "SELECT model, tokens_in, tokens_out, created_at FROM llm_calls ORDER BY created_at"
        ).fetchall()

    def _new_bucket():
        return {"calls": 0, "tokens_in": 0, "tokens_out": 0, "cost": 0.0}

    total_in = total_out = 0
    total_cost = 0.0
    unknown_cost_models = set()
    by_week = defaultdict(_new_bucket)
    by_month = defaultdict(_new_bucket)
    by_model = defaultdict(_new_bucket)

    for c in calls:
        price_in, price_out = price_per_million(c["model"])
        if price_in is not None:
            cost = (c["tokens_in"] / 1_000_000) * price_in + (c["tokens_out"] / 1_000_000) * price_out
        else:
            cost = None
            unknown_cost_models.add(c["model"])

        dt = datetime.fromisoformat(c["created_at"])
        iso_year, iso_week, _ = dt.isocalendar()
        week_key = f"{iso_year}-W{iso_week:02d}"
        month_key = dt.strftime("%Y-%m")

        total_in += c["tokens_in"]
        total_out += c["tokens_out"]
        total_cost += cost or 0

        for bucket, key in ((by_week, week_key), (by_month, month_key), (by_model, c["model"])):
            b = bucket[key]
            b["calls"] += 1
            b["tokens_in"] += c["tokens_in"]
            b["tokens_out"] += c["tokens_out"]
            b["cost"] += cost or 0

    return render_template(
        "costs.html",
        has_data=bool(calls),
        total_calls=len(calls),
        total_in=total_in,
        total_out=total_out,
        total_cost=total_cost,
        by_week=sorted(by_week.items(), reverse=True),
        by_month=sorted(by_month.items(), reverse=True),
        by_model=sorted(by_model.items()),
        unknown_cost_models=sorted(unknown_cost_models),
        active_tab="costs",
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

    idiomas_usuario = {}
    for idioma in ("ingles", "frances", "aleman", "italiano", "portugues"):
        nivel = form.get(f"idioma_{idioma}", "").strip().upper()
        if nivel and nivel not in NIVELES_MCER:
            errors.append(f"Nivel de {idioma} no válido.")
            nivel = ""
        idiomas_usuario[idioma] = nivel

    idiomas_usuario_otros = []
    for line in form.get("idiomas_usuario_otros", "").splitlines():
        line = line.strip()
        if not line:
            continue
        idioma, _, nivel = line.partition(":")
        idioma = idioma.strip().lower()
        nivel = nivel.strip().upper()
        if not idioma or nivel not in NIVELES_MCER:
            errors.append(f"Línea de 'otros idiomas' inválida: «{line}» (formato: idioma: nivel).")
            continue
        idiomas_usuario_otros.append({"idioma": idioma, "nivel": nivel})

    if errors:
        return None, errors

    return {
        "search_queries": queries,
        "search_countries": countries,
        "results_per_query": results_per_query,
        "openrouter_model": model,
        "score_threshold": score_threshold,
        "cv_context": cv_context,
        "idiomas_usuario": idiomas_usuario,
        "idiomas_usuario_otros": idiomas_usuario_otros,
    }, []


def _keys_context():
    stored = load_secrets()
    return {
        "stored_rapidapi": bool(stored.get("rapidapi_key")),
        "stored_openrouter": bool(stored.get("openrouter_key")),
        "env_rapidapi": bool(os.environ.get("RAPIDAPI_KEY")),
        "env_openrouter": bool(os.environ.get("OPENROUTER_API_KEY")),
    }


@app.route("/config", methods=["GET", "POST"])
def config_edit():
    saved_keys = request.args.get("saved_keys") == "1"
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
                "idiomas_usuario": {
                    idioma: request.form.get(f"idioma_{idioma}", "").strip().upper()
                    for idioma in ("ingles", "frances", "aleman", "italiano", "portugues")
                },
                "idiomas_usuario_otros_raw": request.form.get("idiomas_usuario_otros", ""),
            }
            return render_template("config_edit.html", cfg=raw_cfg, errors=errors, saved=False, saved_keys=saved_keys, active_tab="config", **_keys_context())
        save_config(cfg)
        return redirect(url_for("config_edit", saved=1))

    cfg = load_config()
    saved = request.args.get("saved") == "1"
    return render_template("config_edit.html", cfg=cfg, errors=[], saved=saved, saved_keys=saved_keys, active_tab="config", **_keys_context())


# ---------------------------------------------------------------------------
# Claves API — guardadas localmente, sin depender de exportarlas a mano
# ---------------------------------------------------------------------------

@app.route("/keys", methods=["GET", "POST"])
def keys_edit():
    if request.method == "POST":
        current = load_secrets()
        rapidapi_key = request.form.get("rapidapi_key", "").strip()
        openrouter_key = request.form.get("openrouter_key", "").strip()
        # campo vacio = no tocar el valor ya guardado (evita borrar sin querer)
        save_secrets({
            "rapidapi_key": rapidapi_key or current.get("rapidapi_key", ""),
            "openrouter_key": openrouter_key or current.get("openrouter_key", ""),
        })
        return redirect(url_for("config_edit", saved_keys=1))

    return redirect(url_for("config_edit"))


# ---------------------------------------------------------------------------
# Lanzar busqueda + streaming de log
# ---------------------------------------------------------------------------

_run_lock = threading.Lock()
_run_state = {"process": None, "running": False, "log_lines": [], "run_id": None}
_RUN_ID_RE = re.compile(r"^\[run (\d+)\]")


def _reader_thread(proc):
    for line in proc.stdout:
        m = _RUN_ID_RE.match(line)
        with _run_lock:
            if m:
                _run_state["run_id"] = int(m.group(1))
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
        run_id = _run_state["run_id"]

    cfg = load_config()
    results = []
    with get_db() as conn:
        if run_id and not running:
            results = [dict(r) for r in conn.execute(
                "SELECT * FROM jobs WHERE run_id = ? ORDER BY score DESC", (run_id,)
            ).fetchall()]
            attach_lists(conn, results)
            attach_language_flags(results, user_languages(cfg))
        lists = all_lists(conn)

    return render_template(
        "run.html",
        running=running,
        log_lines=log_lines,
        run_id=run_id,
        results=results,
        all_lists=lists,
        cfg=cfg,
        active_tab="run",
    )


@app.route("/run/start", methods=["POST"])
def run_start():
    queries_raw = request.form.get("queries", "").strip()
    countries_checked = request.form.getlist("countries")
    countries_extra = [c.strip() for c in request.form.get("countries_extra", "").split(",") if c.strip()]
    results_per_query = request.form.get("results_per_query", "").strip()

    with _run_lock:
        if _run_state["running"]:
            return redirect(url_for("run_page"))

        cmd = [sys.executable, str(JOB_RADAR_PATH)]
        if queries_raw:
            queries = [q.strip() for q in queries_raw.splitlines() if q.strip()]
            cmd += ["--queries", "|".join(queries)]
        countries = countries_checked + countries_extra
        if countries:
            cmd += ["--countries", ",".join(countries)]
        if results_per_query:
            cmd += ["--results-per-query", results_per_query]

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
        _run_state["run_id"] = None
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
