# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

Radar de ofertas de empleo en dos scripts. `job_radar.py` busca ofertas nuevas (LinkedIn/Indeed/Glassdoor via JSearch/RapidAPI), descarta las ya vistas, las puntua con un LLM via OpenRouter segun el CV/preferencias definidos en el propio script, y guarda los matches en `jobs.db` (SQLite) con `status='active'`. `review_server.py` sirve esas ofertas activas en una pagina local (`http://127.0.0.1:8765`) donde se marcan como "ya postule" o "no interesado" (con feedback opcional), lo que las saca de la lista de activas. Pensado para correrse via cron (ej. diario) en una RPi4/PC.

## Running

```bash
export RAPIDAPI_KEY="tu_key_de_rapidapi"
export OPENROUTER_API_KEY="tu_key_de_openrouter"
pip install requests --break-system-packages
python3 job_radar.py
python3 review_server.py   # abre http://127.0.0.1:8765 para revisar/actuar sobre las ofertas activas
```

Cron diario tipico:
```
0 8 * * * cd /ruta/al/script && /usr/bin/python3 job_radar.py >> radar.log 2>&1
```

No hay tests ni build; dependencias mas alla de la libreria estandar solo `requests` (usado por `job_radar.py`; `review_server.py` es 100% stdlib).

## Architecture

**Busqueda y scoring** (`job_radar.py`), pipeline lineal de 3 pasos:

1. **Busqueda** (`fetch_jobs`, `collect_all_jobs`) — llama a la API JSearch (RapidAPI) por cada combinacion de `SEARCH_QUERIES` x `SEARCH_COUNTRIES`, agregando resultados de LinkedIn/Indeed/Glassdoor/ZipRecruiter.
2. **Dedupe** (`load_seen_ids`, `save_seen_ids`) — compara `job_id` contra `seen_jobs.json` (generado en el mismo directorio, no versionado) para no re-procesar ofertas ya vistas en ejecuciones previas.
3. **Scoring** (`score_job`) — por cada oferta nueva, envia titulo/empresa/descripcion junto con `CV_CONTEXT` a OpenRouter (modelo en `OPENROUTER_MODEL`) pidiendo un JSON `{"score": 0-100, "razon": "..."}`.

Las ofertas con `score >= 65` se guardan en `jobs.db` (`init_db`, `save_active_jobs`) con `status='active'`. `main()` orquesta el flujo y persiste `seen_jobs.json` al final, incluyendo las ofertas nuevas aunque no hayan superado el umbral (para no re-evaluarlas en la siguiente corrida).

**Revision interactiva** (`review_server.py`) — servidor HTTP local (stdlib, sin JS) que lee `jobs.db` y renderiza las ofertas `status='active'` como tarjetas con dos formularios cada una (POST a `/action`): "Ya postule" y "No interesado" (con textarea de feedback opcional). La accion actualiza `status`/`feedback`/`decided_at` en `jobs.db` y redirige (303) de vuelta a `/`, asi que la oferta desaparece de la lista tras decidir.

## Configuration

Toda la configuracion esta al principio de `job_radar.py`, como constantes en mayusculas:
- `SEARCH_QUERIES` / `SEARCH_COUNTRIES` / `RESULTS_PER_QUERY` — que buscar y donde (paises UE).
- `OPENROUTER_MODEL` — modelo usado para puntuar encaje.
- `CV_CONTEXT` — resumen de perfil/preferencias que el LLM usa para evaluar cada oferta; ajustar aqui para cambiar el filtrado. El feedback de "no interesado" (columna `feedback` en `jobs.db`) no se inyecta automaticamente en el prompt — revisalo de vez en cuando y ajusta `CV_CONTEXT` a mano si detectas un patron.

Claves de API (`RAPIDAPI_KEY`, `OPENROUTER_API_KEY`) se leen solo de variables de entorno, nunca hardcodeadas. `review_server.py` usa `HOST`/`PORT` (por defecto `127.0.0.1:8765`) al principio del archivo.
