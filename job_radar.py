#!/usr/bin/env python3
"""
job_radar.py — Radar automatico de ofertas para LinkedIn/Indeed/Glassdoor
=========================================================================

Que hace:
  1. Busca ofertas nuevas via JSearch API (agrega Google for Jobs: LinkedIn,
     Indeed, Glassdoor, ZipRecruiter...).
  2. Descarta las que ya viste (fichero local seen_jobs.json).
  3. Manda las nuevas a OpenRouter junto con tu CV/preferencias para que
     un modelo las puntue y te diga por que encajan (o no).
  4. Guarda los matches en jobs.db (SQLite) con status='active'. Revisalos
     y actua sobre ellos (aplicado / no interesado) con review_server.py.

Como correrlo:
  export RAPIDAPI_KEY="tu_key_de_rapidapi"
  export OPENROUTER_API_KEY="tu_key_de_openrouter"
  pip install requests --break-system-packages
  python3 job_radar.py
  python3 review_server.py   # revisar/actuar sobre las ofertas activas

Para automatizarlo en tu RPi4/PC (ej. cron diario a las 8am):
  0 8 * * * cd /ruta/al/script && /usr/bin/python3 job_radar.py >> radar.log 2>&1
"""

import argparse
import json
import os
import sqlite3
import sys
import time
from datetime import datetime
from pathlib import Path

import requests

from config import load_config
from secrets_store import get_api_keys

# ---------------------------------------------------------------------------
# CONFIGURACION — editable desde el dashboard (pestana "Configuracion") o a
# mano en config.json. Sin config.json, se usan los valores por defecto de
# config.py:DEFAULT_CONFIG (mismo comportamiento que antes de esta migracion).
# ---------------------------------------------------------------------------

_CFG = load_config()
SEARCH_QUERIES = _CFG["search_queries"]
SEARCH_COUNTRIES = _CFG["search_countries"]  # codigos ISO pais para la API (UE)
RESULTS_PER_QUERY = _CFG["results_per_query"]
OPENROUTER_MODEL = _CFG["openrouter_model"]
CV_CONTEXT = _CFG["cv_context"]
SCORE_THRESHOLD = _CFG["score_threshold"]

SEEN_FILE = Path(__file__).parent / "seen_jobs.json"
DB_FILE = Path(__file__).parent / "jobs.db"

# ---------------------------------------------------------------------------
# 1. BUSQUEDA DE OFERTAS (JSearch via RapidAPI)
# ---------------------------------------------------------------------------

def fetch_jobs(query: str, country: str, rapidapi_key: str) -> list[dict]:
    url = "https://jsearch.p.rapidapi.com/search-v2"
    headers = {
        "X-RapidAPI-Key": rapidapi_key,
        "X-RapidAPI-Host": "jsearch.p.rapidapi.com",
    }
    params = {
        "query": query,
        "num_pages": "1",
        "country": country,
        "date_posted": "all",
    }
    last_error = None
    for intento in range(3):
        if intento > 0:
            print(f"  timeout, reintentando '{query}'/'{country}' ({intento + 1}/3)...", flush=True)
        try:
            t0 = time.monotonic()
            resp = requests.get(url, headers=headers, params=params, timeout=30)
            resp.raise_for_status()
            print(f"  ok ({time.monotonic() - t0:.1f}s)", flush=True)
            return resp.json().get("data", {}).get("jobs", [])
        except requests.exceptions.Timeout as e:
            last_error = e
            if intento < 2:
                time.sleep(5 * (intento + 1))
    raise last_error


def collect_all_jobs(rapidapi_key: str) -> list[dict]:
    all_jobs = []
    seen_ids_this_run = set()
    for q in SEARCH_QUERIES:
        for country in SEARCH_COUNTRIES:
            print(f"Buscando '{q}' en '{country}'...", flush=True)
            try:
                jobs = fetch_jobs(q, country, rapidapi_key)
            except requests.RequestException as e:
                print(f"[aviso] fallo buscando '{q}' en '{country}': {e}", file=sys.stderr, flush=True)
                continue
            for j in jobs[:RESULTS_PER_QUERY]:
                job_id = j.get("job_id")
                if job_id and job_id not in seen_ids_this_run:
                    seen_ids_this_run.add(job_id)
                    j.setdefault("job_country", country.upper())
                    all_jobs.append(j)
    return all_jobs


# ---------------------------------------------------------------------------
# 2. DEDUPE — no repetir ofertas ya vistas en ejecuciones anteriores
# ---------------------------------------------------------------------------

def load_seen_ids() -> set[str]:
    if SEEN_FILE.exists():
        return set(json.loads(SEEN_FILE.read_text()))
    return set()


def save_seen_ids(ids: set[str]) -> None:
    SEEN_FILE.write_text(json.dumps(sorted(ids)))


# ---------------------------------------------------------------------------
# 3. FILTRADO POR CV VIA OPENROUTER
# ---------------------------------------------------------------------------

def score_job(job: dict, openrouter_key: str) -> dict:
    title = job.get("job_title", "")
    company = job.get("employer_name", "")
    description = (job.get("job_description") or "")[:2000]

    prompt = f"""Este es mi perfil y lo que busco:
{CV_CONTEXT}

Evalua esta oferta de trabajo y responde SOLO con un JSON, sin texto adicional,
con este formato exacto:
{{"score": <0-100>, "razon": "<explicacion breve, 1-2 frases>", "requisitos": "<resumen breve de los requisitos/requerimientos del puesto segun la descripcion>", "ofrece": "<resumen breve de lo que ofrece la empresa (salario, beneficios, remoto, etc.); cadena vacia si la descripcion no lo menciona>"}}

Oferta:
Puesto: {title}
Empresa: {company}
Descripcion: {description}
"""

    resp = requests.post(
        "https://openrouter.ai/api/v1/chat/completions",
        headers={
            "Authorization": f"Bearer {openrouter_key}",
            "Content-Type": "application/json",
        },
        json={
            "model": OPENROUTER_MODEL,
            "messages": [{"role": "user", "content": prompt}],
        },
        timeout=60,
    )
    resp.raise_for_status()
    content = resp.json()["choices"][0]["message"]["content"].strip()
    content = content.removeprefix("```json").removeprefix("```").removesuffix("```").strip()
    try:
        result = json.loads(content)
    except json.JSONDecodeError:
        return {"score": 0, "razon": "no se pudo evaluar (respuesta no valida)", "requisitos": "", "ofrece": ""}
    result.setdefault("requisitos", "")
    result.setdefault("ofrece", "")
    return result


# ---------------------------------------------------------------------------
# 4. BASE DE DATOS DE OFERTAS ACTIVAS
# ---------------------------------------------------------------------------

def _ensure_column(conn: sqlite3.Connection, table: str, column: str, decl: str) -> None:
    cols = {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}
    if column not in cols:
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {decl}")


def init_db() -> None:
    with sqlite3.connect(DB_FILE) as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS jobs (
                job_id TEXT PRIMARY KEY,
                title TEXT,
                company TEXT,
                location TEXT,
                link TEXT,
                score INTEGER,
                razon TEXT,
                status TEXT NOT NULL DEFAULT 'active',
                feedback TEXT,
                first_seen TEXT,
                decided_at TEXT
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS runs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                started_at TEXT NOT NULL,
                finished_at TEXT,
                status TEXT NOT NULL DEFAULT 'running',
                pid INTEGER,
                jobs_found INTEGER,
                jobs_new INTEGER,
                jobs_saved INTEGER,
                error TEXT
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS lists (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL UNIQUE,
                created_at TEXT NOT NULL
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS job_lists (
                job_id TEXT NOT NULL,
                list_id INTEGER NOT NULL,
                added_at TEXT NOT NULL,
                PRIMARY KEY (job_id, list_id)
            )
            """
        )
        # migraciones para DBs creadas antes de estas columnas
        _ensure_column(conn, "jobs", "run_id", "INTEGER")
        _ensure_column(conn, "jobs", "country", "TEXT")
        _ensure_column(conn, "jobs", "requisitos", "TEXT")
        _ensure_column(conn, "jobs", "ofrece", "TEXT")
        _ensure_column(conn, "runs", "queries", "TEXT")
        _ensure_column(conn, "runs", "countries", "TEXT")


def save_active_jobs(scored_jobs: list[dict], run_id: int | None = None) -> None:
    today = datetime.now().strftime("%Y-%m-%d")
    with sqlite3.connect(DB_FILE) as conn:
        conn.executemany(
            """
            INSERT OR IGNORE INTO jobs
                (job_id, title, company, location, link, score, razon, status, first_seen, run_id, country, requisitos, ofrece)
            VALUES (?, ?, ?, ?, ?, ?, ?, 'active', ?, ?, ?, ?, ?)
            """,
            [
                (
                    j.get("job_id"),
                    j.get("job_title"),
                    j.get("employer_name"),
                    j.get("job_location") or "N/D",
                    j.get("job_apply_link", "N/D"),
                    j["score"],
                    j["razon"],
                    today,
                    run_id,
                    j.get("job_country"),
                    j.get("requisitos") or "",
                    j.get("ofrece") or "",
                )
                for j in scored_jobs
            ],
        )


def count_active_jobs() -> int:
    with sqlite3.connect(DB_FILE) as conn:
        return conn.execute("SELECT COUNT(*) FROM jobs WHERE status = 'active'").fetchone()[0]


# ---------------------------------------------------------------------------
# 5. CONTROL DE EJECUCIONES — evita correr dos busquedas en paralelo
#    (protege tanto cron+cron como cron+dashboard como dashboard+dashboard)
# ---------------------------------------------------------------------------

def start_run(queries: list[str] | None = None, countries: list[str] | None = None) -> int:
    with sqlite3.connect(DB_FILE) as conn:
        cur = conn.execute(
            "INSERT INTO runs (started_at, status, pid, queries, countries) VALUES (?, 'running', ?, ?, ?)",
            (
                datetime.now().isoformat(timespec="seconds"),
                os.getpid(),
                json.dumps(queries) if queries else None,
                json.dumps(countries) if countries else None,
            ),
        )
        return cur.lastrowid


def finish_run(run_id: int, status: str, jobs_found=None, jobs_new=None, jobs_saved=None, error=None) -> None:
    with sqlite3.connect(DB_FILE) as conn:
        conn.execute(
            """
            UPDATE runs
            SET finished_at = ?, status = ?, jobs_found = ?, jobs_new = ?, jobs_saved = ?, error = ?
            WHERE id = ?
            """,
            (datetime.now().isoformat(timespec="seconds"), status, jobs_found, jobs_new, jobs_saved, error, run_id),
        )


def _pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return True


def _check_no_concurrent_run() -> None:
    with sqlite3.connect(DB_FILE) as conn:
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            "SELECT id, pid FROM runs WHERE status = 'running' ORDER BY id DESC LIMIT 1"
        ).fetchone()
    if row is None:
        return
    if row["pid"] and _pid_alive(row["pid"]):
        sys.exit(f"Ya hay una busqueda en curso (run {row['id']}, pid {row['pid']}).")
    # el proceso murio sin marcar su propio run como terminado (crash, kill, etc.)
    finish_run(row["id"], "error", error="proceso interrumpido (pid ya no existe)")


# ---------------------------------------------------------------------------
# MAIN
# ---------------------------------------------------------------------------

def parse_args(argv=None):
    parser = argparse.ArgumentParser(description="Job radar - busqueda y scoring de ofertas")
    parser.add_argument(
        "--queries",
        help="Busquedas para ESTA corrida, separadas por '|' (override puntual, no toca config.json)",
    )
    parser.add_argument(
        "--countries",
        help="Codigos ISO de pais para ESTA corrida, separados por coma (override puntual)",
    )
    parser.add_argument(
        "--results-per-query",
        type=int,
        help="Resultados por busqueda para ESTA corrida (override puntual)",
    )
    return parser.parse_args(argv)


def main():
    global SEARCH_QUERIES, SEARCH_COUNTRIES, RESULTS_PER_QUERY

    args = parse_args()
    if args.queries:
        SEARCH_QUERIES = [q.strip() for q in args.queries.split("|") if q.strip()]
    if args.countries:
        SEARCH_COUNTRIES = [c.strip().lower() for c in args.countries.split(",") if c.strip()]
    if args.results_per_query:
        RESULTS_PER_QUERY = args.results_per_query

    rapidapi_key, openrouter_key = get_api_keys()
    if not rapidapi_key or not openrouter_key:
        sys.exit(
            "Faltan RAPIDAPI_KEY y/o OPENROUTER_API_KEY: exportalas como variables "
            "de entorno o guardalas desde la pestana 'Claves API' del dashboard."
        )

    init_db()
    _check_no_concurrent_run()
    run_id = start_run(SEARCH_QUERIES, SEARCH_COUNTRIES)
    print(f"[run {run_id}] buscando {SEARCH_QUERIES} en {SEARCH_COUNTRIES}...", flush=True)

    try:
        jobs = collect_all_jobs(rapidapi_key)

        seen_ids = load_seen_ids()
        new_jobs = [j for j in jobs if j.get("job_id") not in seen_ids]
        print(f"{len(jobs)} ofertas encontradas, {len(new_jobs)} nuevas.")

        if not new_jobs:
            print("Nada nuevo esta vez.")
            finish_run(run_id, "ok", jobs_found=len(jobs), jobs_new=0, jobs_saved=0)
            return

        print("Evaluando encaje con tu CV via OpenRouter...")
        scored = []
        for j in new_jobs:
            result = score_job(j, openrouter_key)
            scored.append({**j, **result})

        # solo nos interesan matches con encaje minimo razonable
        good_matches = [j for j in scored if j["score"] >= SCORE_THRESHOLD]

        if good_matches:
            save_active_jobs(good_matches, run_id)
            print(f"{len(good_matches)} ofertas nuevas anadidas a jobs.db.")
        else:
            print(f"Ninguna de las nuevas alcanza el umbral de encaje ({SCORE_THRESHOLD}).")

        print(f"Ofertas activas pendientes de revisar: {count_active_jobs()}")
        print("Revisalas con: python3 dashboard.py")

        save_seen_ids(seen_ids | {j.get("job_id") for j in new_jobs if j.get("job_id")})

        finish_run(run_id, "ok", jobs_found=len(jobs), jobs_new=len(new_jobs), jobs_saved=len(good_matches))
    except SystemExit:
        raise
    except Exception as e:
        finish_run(run_id, "error", error=str(e))
        raise


if __name__ == "__main__":
    main()
