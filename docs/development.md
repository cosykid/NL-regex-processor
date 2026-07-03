# Development

## Option A — Docker, production-like (one command)

```bash
cp .env.example .env        # then set DATABASE_URL to your Neon connection string
docker compose up --build
```

- Frontend: http://localhost:8080
- API: http://localhost:8000/api
- Logs: `docker compose logs -f worker`
- Shell: `docker compose exec backend bash`
- Stop: `docker compose down` (add `-v` to wipe named volumes; uploads/results
  live in object storage and the database in Neon, so there's no data volume to lose).

In this base stack the code is **baked into the image** (`COPY . .`) and run by
gunicorn / celery / nginx, so a code edit only takes effect after a rebuild of
that service (`docker compose up --build -d backend`). That's correct for a
demo, but slow for iterating — use Option B.

## Option B — Docker dev overlay (no rebuilds) ⭐

`docker-compose.dev.yml` mounts your source into the containers and swaps in
auto-reloading processes. It is **opt-in** (not auto-applied), so Option A stays
the production-like path. Bring it up by stacking both files:

```bash
docker compose -f docker-compose.yml -f docker-compose.dev.yml up
```

Now edits are picked up **without `docker compose build`**:

| Service | Dev behaviour | What an edit needs |
|---------|---------------|--------------------|
| `backend` (web) | `gunicorn --reload` (poll engine) | **nothing** — save and it reloads |
| `frontend` | Vite dev server with hot module reload | **nothing** — save and the browser updates |
| `worker` (Celery) | live-mounted source | `docker compose -f docker-compose.yml -f docker-compose.dev.yml restart worker` (~3 s) — Celery imports tasks at startup, so it needs a restart, never a rebuild |

> Why `gunicorn --reload` and not Django's `runserver`? `runserver`'s
> StatReloader restarts the **whole process** on a file change, and once the web
> process has loaded native libraries (DuckDB, or py4j via PySpark) that restart
> aborts with a native `SIGABRT` — taking the container down instead of
> reloading. Gunicorn's arbiter never imports the app (only its workers do), so a
> worker that aborts on reload is simply respawned and the container stays up.
> `--reload-engine=poll` is used because inotify doesn't see host edits through
> the macOS bind mount. (Host mode, Option C, keeps `runserver` — no bind mount,
> no crash.)

> Tip: alias `dc='docker compose -f docker-compose.yml -f docker-compose.dev.yml'`
> so it's just `dc up`, `dc restart worker`, `dc logs -f worker`.

**You only need `--build` again when *dependencies* change** —
`backend/requirements.txt` or `frontend/package.json`. Pure code edits never do.

## Option C — Local (no Docker)

The tightest loop of all (host `runserver` + host `npm run dev`), but you manage
the toolchain yourself: **Python 3.11**, **Node 20**, a **Java 17 JRE** (for
Spark), and a local **Redis**. Set **`DATABASE_URL`** to your Neon connection
string — Neon is
the platform database, required everywhere; there is no SQLite fallback. The
`pytest` suite runs against it too, via an isolated `test_<db>` database.

```bash
# backend
python3.11 -m venv .venv && source .venv/bin/activate
pip install -r backend/requirements.txt
cd backend
python manage.py migrate
python manage.py runserver            # http://localhost:8000

# celery worker (separate shell, same venv, from backend/)
celery -A config worker --loglevel=info

# frontend (separate shell)
cd frontend && npm install && npm run dev   # http://localhost:5173 (proxies /api → :8000)
```

> The Vite dev server proxies `/api` and `/healthz` to `http://localhost:8000`
> (override with `BACKEND_ORIGIN`).

## Project layout

```
backend/
  config/        Django project (settings/ package, celery, urls, wsgi/asgi)
  api/           DRF serializers, views/ (uploads, jobs), exports, params, urls, pagination
  jobs/          models (UploadedFile, Job) + migrations
  processing/    tasks, ingest, spark_engine, llm, regex_safety, cache, file_inspect, results, storage
  tests/         pytest suite
  Dockerfile · entrypoint.sh · requirements.txt · pytest.ini
frontend/          (TypeScript)
  src/           App.tsx, main.tsx, api/, lib/, hooks/, components/ (+ grid/), styles/
  Dockerfile · nginx.conf · vite.config.ts · tsconfig*.json · package.json
scripts/         generate_dataset.py
samples/         contacts.csv
docs/            this documentation
docker-compose.yml · .env.example
```

## Tests

```bash
cd backend
pip install -r requirements.txt   # if not already
pytest            # needs DATABASE_URL — tests run on Postgres (test_<db>), not SQLite
```

Tests run against the **same Postgres** as the app (Django creates an isolated
`test_<db>` database). It's kept between runs — `--reuse-db` in `pytest.ini` —
because dropping it through Neon's pooler is unreliable and slow; after a model
or migration change, refresh once with `pytest --create-db`. Storage-touching
tests are pinned to the `local` backend, so they never reach S3.

Coverage:

| File | Tests |
|------|-------|
| `tests/test_regex_safety.py` | validation, invalid/empty patterns, nested-quantifier rejection, length cap, replacement escaping |
| `tests/test_llm_heuristic.py` | heuristic resolution (email/phone/quoted/unknown), generated patterns pass validation |
| `tests/test_llm_context.py` | data-sample rendering, cache-context signature (same prompt over different data / actions doesn't collide), predicate validation, action-specialised prompts |
| `tests/test_action_resolution.py` | `_resolve_action`: explicit action wins, `auto` defers to the model, typed value beats inferred, bogus action falls back to `replace` |
| `tests/test_spark_conditions.py` | `build_match_condition` AND/OR combining and `cell_action_expr` branches, via a symbolic stand-in for `pyspark.sql.functions` (no JVM) |
| `tests/test_file_inspect.py` | CSV + Excel header/preview extraction; cursor-paged raw windows (byte-offset resume, quoted-newline continuity) |
| `tests/test_storage.py` | local backend round-trip; S3 locator/URI/Hadoop-config mapping; `inspect`/`read_window` over a binary stream |
| `tests/test_results_page.py` | paged reads: row numbering from the page offset, original numbering preserved under affected-only, match flag surfaced/stripped, persisted counts reused |
| `tests/test_results_export.py` | Parquet → CSV/Excel export, cell sanitising, affected-only, Excel row/column ceilings |
| `tests/test_api.py` | upload, raw-row windowing + bad-cursor 400, job create + validation, results-not-ready 409, cancel |
| `tests/test_cleanup.py` | `post_delete` storage cleanup — upload file, job result dir, cascade delete, best-effort on storage error |

The Spark engine requires a JVM and is exercised via the Docker stack (and the
manual end-to-end runs); the pure-Python layers are covered by the suite above.
The API tests stub the Celery dispatch so they don't require a running worker.

## Common tasks

```bash
# regenerate migrations after a model change
docker compose exec backend python manage.py makemigrations

# open a Django shell against the configured database
docker compose exec backend python manage.py shell

# generate a large test file, then upload it via the UI/API
python scripts/generate_dataset.py --rows 1000000 --out /tmp/big.csv
```

## Conventions

- Configuration is environment-driven (see [configuration.md](configuration.md));
  no settings are hard-coded per environment.
- `pyspark` and `anthropic` are imported lazily inside functions so the Django
  app and tests import without a JVM or the LLM SDK present.
- State transitions use `Job.objects.filter(id=...).update(...)` to avoid
  clobbering concurrent writes from the progress poller.
- The frontend is **TypeScript** (strict mode). `npm run build` runs
  `tsc --noEmit` (app + `vite.config.ts`) before bundling with Vite; use
  `npm run type-check` for a types-only pass. Shared API/domain types live in
  `frontend/src/lib/api-types.ts`.
