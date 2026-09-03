🌐 [English](README.md) | Español

# Job Radar

Radar automático de ofertas de empleo en dos scripts, pensado para correr a
diario (por ejemplo por cron en una Raspberry Pi) sin intervención manual
salvo para revisar los resultados.

## Qué hace

1. **Busca** ofertas nuevas vía [JSearch](https://rapidapi.com/letscrape-6bRBa3QguO5/api/jsearch)
   (RapidAPI), que agrega LinkedIn, Indeed, Glassdoor y ZipRecruiter a partir
   de Google for Jobs.
2. **Descarta** las que ya se vieron en ejecuciones anteriores (`seen_jobs.json`,
   local, no versionado).
3. **Puntúa** cada oferta nueva con un LLM vía OpenRouter, comparándola con tu
   CV/preferencias (definidos como texto en el propio script), y guarda un
   score 0-100 con la razón.
4. **Guarda** las ofertas con score ≥ 65 en `jobs.db` (SQLite) con
   `status='active'`.
5. **Revísalas** con `review_server.py`, un servidor web local sin
   dependencias donde marcas cada oferta como "ya postulé" o "no interesado"
   (con feedback opcional) y desaparece de la lista de activas.

## Uso

```bash
export RAPIDAPI_KEY="tu_key_de_rapidapi"
export OPENROUTER_API_KEY="tu_key_de_openrouter"
pip install requests --break-system-packages

python3 job_radar.py       # busca, puntúa y guarda matches nuevos
python3 review_server.py   # abre http://127.0.0.1:8765 para revisar/actuar
```

### Automatizarlo (cron diario)

```
0 8 * * * cd /ruta/al/script && /usr/bin/python3 job_radar.py >> radar.log 2>&1
```

No hay build ni tests: solo estándar de Python, salvo `requests` para
`job_radar.py` (`review_server.py` es 100% librería estándar, sin JS en el
frontend).

## Arquitectura

**Búsqueda y scoring** (`job_radar.py`), pipeline lineal de tres pasos:

1. **Búsqueda** (`fetch_jobs`, `collect_all_jobs`) — llama a JSearch por cada
   combinación de `SEARCH_QUERIES` × `SEARCH_COUNTRIES`.
2. **Dedupe** (`load_seen_ids`, `save_seen_ids`) — compara `job_id` contra
   `seen_jobs.json` para no reprocesar ofertas ya vistas.
3. **Scoring** (`score_job`) — envía título/empresa/descripción junto con el
   contexto de tu CV a OpenRouter, pidiendo un JSON `{"score": 0-100, "razon": "..."}`.

Las ofertas con `score >= 65` se guardan en `jobs.db` con `status='active'`.
`main()` orquesta el flujo y persiste `seen_jobs.json` al final (incluyendo
las que no superaron el umbral, para no reevaluarlas en la siguiente corrida).

**Revisión interactiva** (`review_server.py`) — servidor HTTP local (stdlib,
sin JS) que lee `jobs.db` y renderiza las ofertas activas como tarjetas con
dos formularios cada una (POST a `/action`): "Ya postulé" y "No interesado"
(con textarea de feedback). La acción actualiza `status`/`feedback`/`decided_at`
en `jobs.db` y redirige (303) de vuelta a `/`.

## Configuración

Toda la configuración está al principio de `job_radar.py`, como constantes en
mayúsculas:

- `SEARCH_QUERIES` / `SEARCH_COUNTRIES` / `RESULTS_PER_QUERY` — qué buscar y
  dónde.
- `OPENROUTER_MODEL` — modelo usado para puntuar el encaje.
- `CV_CONTEXT` — resumen de perfil/preferencias que el LLM usa para evaluar
  cada oferta; edítalo aquí para cambiar el filtrado. El feedback de "no
  interesado" no se inyecta automáticamente en el prompt — revísalo de vez en
  cuando y ajusta `CV_CONTEXT` a mano si detectas un patrón.

Las claves de API (`RAPIDAPI_KEY`, `OPENROUTER_API_KEY`) se leen solo de
variables de entorno, nunca hardcodeadas.

## Notas

- `jobs.db`, `seen_jobs.json` y `reports/` son estado local del usuario y no
  se versionan (ver `.gitignore`).
- `review_server.py` escucha solo en `127.0.0.1` por defecto — no expone nada
  fuera de la máquina donde corre.

## Licencia

[MIT](LICENSE).
