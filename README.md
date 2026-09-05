🌐 English | [Español](README.es.md)

# Job Radar

Automatic job-offer radar, meant to run daily (e.g. via cron on a Raspberry
Pi) with a local web dashboard to manage everything from one place:
reviewing offers, launching manual searches, editing your CV/preferences,
and viewing stats.

## What it does

1. **Searches** for new job offers via [JSearch](https://rapidapi.com/letscrape-6bRBa3QguO5/api/jsearch)
   (RapidAPI), which aggregates LinkedIn, Indeed, Glassdoor and ZipRecruiter
   through Google for Jobs.
2. **Discards** offers already seen in previous runs (`seen_jobs.json`,
   local, not version-controlled).
3. **Scores** each new offer with an LLM via OpenRouter, comparing it
   against your CV/preferences (editable from the dashboard), and stores a
   0-100 score with the reasoning.
4. **Saves** offers above the configured threshold into `jobs.db` (SQLite)
   with `status='active'`.
5. **Review and manage everything** with `dashboard.py`: mark offers as
   "applied"/"not interested", launch manual searches with a live log, view
   history/stats, and edit the configuration — all in one place.

## Usage

```bash
export RAPIDAPI_KEY="your_rapidapi_key"
export OPENROUTER_API_KEY="your_openrouter_key"
pip install -r requirements.txt --break-system-packages

python3 job_radar.py    # search, score, and save new matches (cron)
python3 dashboard.py    # open http://127.0.0.1:8765 — full dashboard
```

### Automating it (daily cron)

```
0 8 * * * cd /path/to/script && /usr/bin/python3 job_radar.py >> radar.log 2>&1
```

The dashboard doesn't need to be running for the cron job to work — or you
can trigger a search manually from the "Run search" tab instead of waiting
for the cron. Both paths share the same guard: two searches can't run in
parallel.

Dependencies: `requests` and `flask` (see `requirements.txt`). No build, no
tests.

## Architecture

**Search and scoring** (`job_radar.py`), a linear pipeline:

1. **Search** (`fetch_jobs`, `collect_all_jobs`) — calls JSearch for every
   combination of `SEARCH_QUERIES` × `SEARCH_COUNTRIES` (from `config.json`).
2. **Dedupe** (`load_seen_ids`, `save_seen_ids`) — compares `job_id` against
   `seen_jobs.json` to avoid reprocessing offers already seen.
3. **Scoring** (`score_job`) — sends title/company/description along with
   your CV context to OpenRouter, requesting a JSON `{"score": 0-100, "razon": "..."}`.

Offers with `score >= SCORE_THRESHOLD` are saved to `jobs.db` with
`status='active'`. `main()` orchestrates the flow, logs each run in the
`runs` table (used by both the concurrency guard and the stats view), and
persists `seen_jobs.json` at the end.

**Dashboard** (`dashboard.py`, Flask) — local server on `127.0.0.1:8765`
with five views:

- **Active** (`/`, `/action`) — offer cards with score, same as before:
  "Applied"/"Not interested" (with optional feedback).
- **Run search** (`/run`) — runs `job_radar.py` as a subprocess on demand,
  with its log streamed live (Server-Sent Events).
- **History** (`/history`) — offers already decided on, with their feedback.
- **Stats** (`/stats`) — counts by status, score distribution, and the
  result of the last run.
- **Configuration** (`/config`) — edit search queries, countries,
  threshold, model and `CV_CONTEXT` without touching code; persists to
  `config.json`.

## Configuration

The editable configuration lives in `config.json` (generated/edited from
the dashboard's "Configuration" tab; if it doesn't exist yet, the defaults
in `config.py:DEFAULT_CONFIG` are used and everything works as before):

- `search_queries` / `search_countries` / `results_per_query` — what to
  search for and where.
- `openrouter_model` — model used to score the fit.
- `score_threshold` — minimum score for an offer to be saved as active.
- `cv_context` — profile/preferences summary the LLM uses to evaluate each
  offer. "Not interested" feedback is not automatically injected into the
  prompt — review it occasionally (History tab) and adjust `cv_context` by
  hand if you spot a pattern.

`job_radar.py` only **reads** `config.json` (never writes) — only the
dashboard modifies it, avoiding races between the cron job and a concurrent
edit.

API keys (`RAPIDAPI_KEY`, `OPENROUTER_API_KEY`) are read only from
environment variables, never saved to `config.json` or editable from the
web.

## Notes

- `jobs.db`, `seen_jobs.json`, `config.json` and `reports/` are local user
  state and are not version-controlled (see `.gitignore`).
- `dashboard.py` listens only on `127.0.0.1` by default — it exposes
  nothing outside the machine it runs on.

## License

[MIT](LICENSE).
