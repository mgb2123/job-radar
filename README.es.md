🌐 [English](README.md) | Español

# Job Radar

Radar automático de ofertas de empleo, pensado para correr a diario (por
ejemplo por cron en una Raspberry Pi) con un dashboard web local para
manejar toda la lógica desde un mismo sitio: revisar ofertas, lanzar
búsquedas manuales, editar tu CV/preferencias y ver estadísticas.

## Qué hace

1. **Busca** ofertas nuevas vía [JSearch](https://rapidapi.com/letscrape-6bRBa3QguO5/api/jsearch)
   (RapidAPI), que agrega LinkedIn, Indeed, Glassdoor y ZipRecruiter a partir
   de Google for Jobs.
2. **Descarta** las que ya se vieron en ejecuciones anteriores (`seen_jobs.json`,
   local, no versionado).
3. **Puntúa** cada oferta nueva con un LLM vía OpenRouter, comparándola con tu
   CV/preferencias (editables desde el dashboard), y guarda un score 0-100
   con la razón.
4. **Guarda** las ofertas que superan el umbral configurado en `jobs.db`
   (SQLite) con `status='active'`.
5. **Revísalas y manéjalo todo** con `dashboard.py`: marcar ofertas como "ya
   postulé"/"no interesado", lanzar búsquedas manuales con log en vivo, ver
   historial/estadísticas y editar la configuración — todo en un mismo sitio.

## Uso

```bash
export RAPIDAPI_KEY="tu_key_de_rapidapi"
export OPENROUTER_API_KEY="tu_key_de_openrouter"
pip install -r requirements.txt --break-system-packages

python3 job_radar.py    # busca, puntúa y guarda matches nuevos (cron)
python3 dashboard.py    # abre http://127.0.0.1:8765 — panel completo
```

### Automatizarlo (cron diario)

```
0 8 * * * cd /ruta/al/script && /usr/bin/python3 job_radar.py >> radar.log 2>&1
```

El dashboard no hace falta tenerlo abierto para que el cron funcione — o
puedes lanzar la búsqueda a mano desde la pestaña "Lanzar búsqueda" en vez
de esperar al cron. Ambos caminos comparten la misma guardia: no pueden
correr dos búsquedas en paralelo.

Dependencias: `requests` y `flask` (ver `requirements.txt`). Sin build ni
tests.

## Arquitectura

**Búsqueda y scoring** (`job_radar.py`), pipeline lineal:

1. **Búsqueda** (`fetch_jobs`, `collect_all_jobs`) — llama a JSearch por cada
   combinación de `SEARCH_QUERIES` × `SEARCH_COUNTRIES` (de `config.json`).
2. **Dedupe** (`load_seen_ids`, `save_seen_ids`) — compara `job_id` contra
   `seen_jobs.json` para no reprocesar ofertas ya vistas.
3. **Scoring** (`score_job`) — envía título/empresa/descripción junto con el
   contexto de tu CV a OpenRouter, pidiendo un JSON `{"score": 0-100, "razon": "..."}`.

Las ofertas con `score >= SCORE_THRESHOLD` se guardan en `jobs.db` con
`status='active'`. `main()` orquesta el flujo, registra cada ejecución en la
tabla `runs` (para la guardia de concurrencia y las estadísticas) y persiste
`seen_jobs.json` al final.

**Dashboard** (`dashboard.py`, Flask) — servidor local en
`127.0.0.1:8765` con cinco vistas:

- **Activas** (`/`, `/action`) — tarjetas de ofertas con score, igual que
  antes: "Ya postulé"/"No interesado" (con feedback opcional).
- **Lanzar búsqueda** (`/run`) — corre `job_radar.py` como subprocess bajo
  demanda, con el log streameado en vivo (Server-Sent Events).
- **Historial** (`/history`) — ofertas ya decididas, con su feedback.
- **Estadísticas** (`/stats`) — contadores por estado, distribución de
  scores y resultado de la última ejecución.
- **Configuración** (`/config`) — editar búsquedas, países, umbral, modelo y
  `CV_CONTEXT` sin tocar código; persiste en `config.json`.

## Configuración

La configuración editable vive en `config.json` (generado/editado desde la
pestaña "Configuración" del dashboard; si no existe, se usan los valores por
defecto de `config.py:DEFAULT_CONFIG` y todo funciona igual que antes):

- `search_queries` / `search_countries` / `results_per_query` — qué buscar y
  dónde.
- `openrouter_model` — modelo usado para puntuar el encaje.
- `score_threshold` — score mínimo para guardar una oferta como activa.
- `cv_context` — resumen de perfil/preferencias que el LLM usa para evaluar
  cada oferta. El feedback de "no interesado" no se inyecta automáticamente
  en el prompt — revísalo de vez en cuando (pestaña Historial) y ajusta
  `cv_context` a mano si detectas un patrón.

`job_radar.py` solo lee `config.json` (nunca escribe) — solo el dashboard lo
modifica, evitando carreras entre el cron y una edición concurrente.

Las claves de API (`RAPIDAPI_KEY`, `OPENROUTER_API_KEY`) se leen solo de
variables de entorno, nunca se guardan en `config.json` ni son editables
desde la web.

## Notas

- `jobs.db`, `seen_jobs.json`, `config.json` y `reports/` son estado local
  del usuario y no se versionan (ver `.gitignore`).
- `dashboard.py` escucha solo en `127.0.0.1` por defecto — no expone nada
  fuera de la máquina donde corre.

## Licencia

[MIT](LICENSE).
