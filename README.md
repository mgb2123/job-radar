🌐 English | [Español](README.es.md)

# Job Radar

Automatic job-offer radar in two scripts, meant to run daily (e.g. via cron
on a Raspberry Pi) with no manual intervention other than reviewing the
results.

## What it does

1. **Searches** for new job offers via [JSearch](https://rapidapi.com/letscrape-6bRBa3QguO5/api/jsearch)
   (RapidAPI), which aggregates LinkedIn, Indeed, Glassdoor and ZipRecruiter
   through Google for Jobs.
2. **Discards** offers already seen in previous runs (`seen_jobs.json`,
   local, not version-controlled).
3. **Scores** each new offer with an LLM via OpenRouter, comparing it against
   your CV/preferences (defined as text in the script itself), and stores a
   0-100 score with the reasoning.
4. **Saves** offers with a score ≥ 65 into `jobs.db` (SQLite) with
   `status='active'`.
5. **Review** them with `review_server.py`, a dependency-free local web
   server where you mark each offer as "applied" or "not interested" (with
   optional feedback), removing it from the active list.

## Usage

```bash
export RAPIDAPI_KEY="your_rapidapi_key"
export OPENROUTER_API_KEY="your_openrouter_key"
pip install requests --break-system-packages

python3 job_radar.py       # search, score, and save new matches
python3 review_server.py   # open http://127.0.0.1:8765 to review/act
```

### Automating it (daily cron)

```
0 8 * * * cd /path/to/script && /usr/bin/python3 job_radar.py >> radar.log 2>&1
```

No build, no tests: pure Python standard library, except `requests` for
`job_radar.py` (`review_server.py` is 100% standard library, no JS on the
frontend).

## Architecture

**Search and scoring** (`job_radar.py`), a linear three-step pipeline:

1. **Search** (`fetch_jobs`, `collect_all_jobs`) — calls JSearch for every
   combination of `SEARCH_QUERIES` × `SEARCH_COUNTRIES`.
2. **Dedupe** (`load_seen_ids`, `save_seen_ids`) — compares `job_id` against
   `seen_jobs.json` to avoid reprocessing offers already seen.
3. **Scoring** (`score_job`) — sends title/company/description along with
   your CV context to OpenRouter, requesting a JSON `{"score": 0-100, "razon": "..."}`.

Offers with `score >= 65` are saved to `jobs.db` with `status='active'`.
`main()` orchestrates the flow and persists `seen_jobs.json` at the end
(including offers below the threshold, so they aren't re-evaluated on the
next run).

**Interactive review** (`review_server.py`) — a local HTTP server (stdlib,
no JS) that reads `jobs.db` and renders active offers as cards with two
forms each (POST to `/action`): "Applied" and "Not interested" (with a
feedback textarea). The action updates `status`/`feedback`/`decided_at` in
`jobs.db` and redirects (303) back to `/`.

## Configuration

All configuration lives at the top of `job_radar.py`, as uppercase
constants:

- `SEARCH_QUERIES` / `SEARCH_COUNTRIES` / `RESULTS_PER_QUERY` — what to
  search for and where.
- `OPENROUTER_MODEL` — model used to score the fit.
- `CV_CONTEXT` — profile/preferences summary the LLM uses to evaluate each
  offer; edit it here to change the filtering. "Not interested" feedback is
  not automatically injected into the prompt — review it occasionally and
  adjust `CV_CONTEXT` by hand if you spot a pattern.

API keys (`RAPIDAPI_KEY`, `OPENROUTER_API_KEY`) are read only from
environment variables, never hardcoded.

## Notes

- `jobs.db`, `seen_jobs.json` and `reports/` are local user state and are
  not version-controlled (see `.gitignore`).
- `review_server.py` listens only on `127.0.0.1` by default — it exposes
  nothing outside the machine it runs on.

## License

[MIT](LICENSE).
