# Distributed NL-to-Regex Data Processing Platform

Upload a CSV/Excel file, describe a pattern **in plain English** (e.g. *"find
email addresses"*), and replace every match across the dataset — at scale.
Natural language is converted to a regex by an LLM, and the replacement runs as
a **distributed Apache Spark** transformation dispatched through **Celery**, so
the request/response cycle never blocks and millions of rows stream through
without loading the file into the web process.

```
"Find email addresses in the Email column and replace them with 'REDACTED'."
        │
        ▼   LLM (cached in Redis)
\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,7}\b
        │
        ▼   Spark regexp_replace across partitions
   john.doe@example.com  ->  REDACTED
```

> **Stack:** Django + DRF · Celery · Redis (broker / result backend / cache) ·
> PySpark · React + TypeScript (Vite) · **Neon** (serverless Postgres) · **S3** (object
> storage, or a local volume) · Docker Compose.

---

## Demo video

📹 **[Watch the demo](docs/demo.md)** — a walk-through of an asynchronous job
running to completion (upload → live progress → paginated results).

> _Replace this link / embed with your recorded video before submitting._

---

## Documentation

In-depth documentation lives in [`docs/`](docs/README.md):
[architecture](docs/architecture.md) ·
[processing pipeline](docs/pipeline.md) ·
[API reference](docs/api.md) ·
[data model](docs/data-model.md) ·
[configuration](docs/configuration.md) ·
[development](docs/development.md) ·
[deployment & operations](docs/deployment.md) ·
[CI/CD & the live deployment](docs/cicd.md).

---

## Table of contents

- [Architecture](#architecture)
- [How a job flows through the system](#how-a-job-flows-through-the-system)
- [Quick start (one command)](#quick-start-one-command)
- [API reference](#api-reference)
- [PySpark engine & partitioning rationale](#pyspark-engine--partitioning-rationale)
- [LLM integration, caching & safety](#llm-integration-caching--safety)
- [Demonstrating scale (large files)](#demonstrating-scale-large-files)
- [Optional: standalone Spark cluster](#optional-standalone-spark-cluster)
- [Observability](#observability)
- [Local development](#local-development)
- [Tests](#tests)
- [Deployment](#deployment)
- [Design decisions & trade-offs](#design-decisions--trade-offs)

---

## Architecture

The system is split into clear layers with a deliberate separation between the
**API**, the **task** layer, and the **data/engine** layer.

```
                          ┌──────────────────────────┐
        Browser  ───────► │  React SPA (nginx)        │
                          └────────────┬─────────────┘
                                       │  /api  (REST, polling)
                          ┌────────────▼─────────────┐
                          │  Django + DRF  (web)      │   ← never does heavy work
                          │  - upload → object storage│
                          │  - create job (returns id)│
                          │  - poll status / results  │
                          └─────┬───────────────┬─────┘
            enqueue task        │               │   read job rows
                                ▼               ▼
                      ┌──────────────┐   ┌──────────────┐
                      │    Redis     │   │  Neon (PG)   │
                      │ broker /     │   │ jobs +       │
                      │ backend /    │   │ uploads      │
                      │ cache        │   └──────────────┘
                      └──────┬───────┘
                             │ deliver task
                  ┌──────────▼─────────────────────────────┐
                  │  Celery worker                          │
                  │   1. NL → regex  (cache → LLM → verify) │
                  │   2. PySpark replacement (partitioned)  │
                  │   3. write Parquet result               │
                  │   + progress updates + cancellation     │
                  └──────────┬──────────────────────────────┘
                             │ read/write
                  ┌──────────▼───────────┐
                  │  Object storage      │  uploads/  results/<job>/
                  │  local volume or S3  │
                  └──────────────────────┘
```

**Layered code structure** (`backend/`):

| Layer | Package | Responsibility |
|-------|---------|----------------|
| API   | `api/` | DRF serializers, views, URLs, pagination — HTTP only |
| Data  | `jobs/` | `UploadedFile` + `Job` models (status, progress, results) |
| Task  | `processing/tasks.py` | Celery orchestration of the pipeline |
| Engine| `processing/spark_engine.py` | PySpark read → transform → write |
| LLM   | `processing/llm.py` + `regex_safety.py` + `cache.py` | NL→regex, validation, Redis cache |
| Ingest| `processing/file_inspect.py` | header/preview + cursor-paged raw reads; `results.py` paged reads |
| Store | `processing/storage.py` | Pluggable object storage — local volume (default) or S3 (see `infra/terraform`) |

---

## How a job flows through the system

1. **Upload** — `POST /api/uploads` streams the file to object storage (a local
   volume, or S3) in chunks (never buffered in the web process) and reads **only
   the header + a small preview** to populate the column picker. Even a multi-GB
   upload is inspected cheaply. The original file can then be scrolled in full
   via `GET /api/uploads/<id>/rows` (cursor-paged windows straight from storage)
   before any transformation is run.
2. **Create job** — `POST /api/jobs` persists a `Job` (`QUEUED`), enqueues a
   Celery task, and **returns a job id immediately**. The endpoint never blocks
   on the LLM or Spark.
3. **Background pipeline** (Celery worker):
   - **Condition generation** is itself background work: check the Redis cache,
     else call the LLM (or the heuristic fallback) to decompose the prompt into
     per-column predicates + an AND/OR combinator, then **validate** every
     pattern (compilability + ReDoS safety) before use.
   - **Action resolution** — each job carries an output `action`: `replace`,
     `mask`, `extract` (cell edits), `keep`/`drop` (row filters), `find`
     (report-only), or `auto` (default), where the model infers the action —
     and any inline value — from the verb in the prompt.
   - **Spark select + apply**: read the file into a DataFrame, combine the
     per-column `rlike` predicates into the row-match condition, and apply the
     resolved action to matched rows as a native, partitioned transformation
     (`regexp_replace` / `regexp_extract` / a row filter), writing the result
     as Parquet.
   - **Progress** is mirrored to both the `Job` row and the Celery task state,
     surfaced through the polling API as a percentage + stage label.
4. **Poll** — the UI polls `GET /api/jobs/<id>` (~1.5 s) for status/progress and
   the resolved regex.
5. **Results** — once `SUCCESS`, `GET /api/jobs/<id>/results?page=…` serves the
   processed data **paginated** (DuckDB reads only the needed Parquet row
   groups — millions of rows are never shipped to the browser at once).

**Failure handling**

- **Transient** errors (LLM/network) → Celery retries with exponential backoff;
  on exhaustion the job is marked `FAILED` with the reason.
- **Permanent** errors (unknown column, unsafe regex, Spark error) → `FAILED`
  immediately (no pointless retries).
- **Cancellation** (`POST /api/jobs/<id>/cancel`) is cooperative: a Redis flag
  is set and the running task + Spark progress poller observe it and unwind to
  `CANCELLED` (the Spark action is aborted via `cancelAllJobs`).

---

## Quick start (one command)

**Prerequisites:** Docker + Docker Compose.

```bash
git clone <your-repo-url> nl-regex-processor
cd nl-regex-processor
cp .env.example .env          # then set DATABASE_URL to your Neon connection string

docker compose up --build
```

> **One required setting:** this build uses **[Neon](https://neon.tech)** as its
> database (no local Postgres container). Paste your Neon *pooled* connection
> string into `.env` as
> `DATABASE_URL=postgresql://…-pooler.…neon.tech/neondb?sslmode=require&channel_binding=require`
> (copy it verbatim — the DB name and params are read from the URL) before
> starting. The `web` container runs `migrate` against Neon on boot.

This brings up Redis, the Django API, the Celery worker (with the bundled Spark
runtime), and the React frontend; the database is your Neon instance.

| Service | URL |
|---------|-----|
| **Frontend** | http://localhost:8080 |
| API | http://localhost:8000/api |
| API health | http://localhost:8000/healthz |

> **No API key needed to try it.** With `ANTHROPIC_API_KEY` unset, the app uses
> a **deterministic heuristic** generator that covers the common entities
> (emails, phones, URLs, dates, numbers, quoted literals, …), so the whole
> pipeline runs end-to-end. Set `ANTHROPIC_API_KEY` in `.env` to switch on
> LLM-powered generation for arbitrary descriptions.

**Try the example from the brief:** upload a small CSV with an `Email` column,
select it, enter *"Find email addresses"*, replacement `REDACTED`, and run.

---

## API reference

| Method | Path | Description |
|--------|------|-------------|
| `POST` | `/api/uploads` | multipart upload → `{id, columns, preview_rows}` |
| `GET`  | `/api/uploads/<id>` | upload metadata |
| `GET`  | `/api/uploads/<id>/rows?cursor=&limit=` | cursor-paged window of the raw file (scroll the original before transforming) |
| `POST` | `/api/jobs` | create job, dispatch async work, **return job id** |
| `GET`  | `/api/jobs` | list jobs (filter one dataset's runs with `?uploaded_file=`) |
| `GET`  | `/api/jobs/<id>` | poll status / progress / resolved regex |
| `POST` | `/api/jobs/<id>/cancel` | request cancellation |
| `GET`  | `/api/jobs/<id>/results?page=&page_size=&matched_only=` | paged processed result (optionally affected rows only) |
| `GET`  | `/api/jobs/<id>/export?fmt=&matched_only=` | download the processed result as CSV or Excel (`fmt=xlsx`) |

**Create-job body**

```json
{
  "uploaded_file": "<upload-uuid>",
  "nl_prompt": "Find email addresses",
  "replacement_value": "REDACTED",
  "target_columns": ["Email"],
  "action": "auto"
}
```

To match across columns, select multiple `target_columns` and describe the
per-column conditions in one prompt — e.g. `"name starts with A and phone starts
with 0"` resolves to two predicates combined with AND (use "or" for OR).
`action` is optional: `auto` (default) infers the output action from the prompt's
verb; an explicit `find`, `replace`, `mask`, `extract`, `keep`, or `drop`
overrides it.

**Job object (polled)** includes `status` (`QUEUED|RUNNING|SUCCESS|FAILED|CANCELLED`),
`progress` (0–100), `stage`, `predicates` (`[{column, pattern, explanation}]`),
`combinator` (`all|any`), `regex_pattern` (a readable summary), `regex_source`
(`cache|llm|heuristic`), `resolved_action` (the concrete action that ran),
`total_rows`, `matched_rows`, `error_message`.

---

## PySpark engine & partitioning rationale

The engine (`processing/spark_engine.py`) applies the regex as **native column
expressions** (`regexp_replace`, `rlike`) rather than Python row UDFs. This is
the key scaling decision: the work executes inside the JVM across partitions
with no per-row Python serialization round-trip, so throughput stays high as the
row count grows into the millions.

**Partitioning choice.** Partitioning is left to Spark's native file splitting
(`spark.sql.files.maxPartitionBytes`, default 128 MB): a large uncompressed CSV
is split by size into one task per split, so work spreads across all cores
(local) or executors (cluster) with no extra step. We do **not** `repartition` —
a forced full shuffle dominated cost on small files and was redundant on large
ones already split.

**Single cached parse.** The source is read/parsed **once** and cached
(`MEMORY_AND_DISK`). The row-count pass materialises the cache and the write
reads from it, so the CSV is never re-parsed per action — previously three full
passes (count, matched-count, write), now one parse plus the write.

**Reading & writing.** Input is read with Spark's CSV reader (Excel is
stream-converted to CSV first, since Excel is inherently bounded). The result is
written as **Parquet** — columnar and splittable — which the read path pages
through efficiently with DuckDB (`LIMIT/OFFSET` touches only the needed row
groups). The browser is never asked to render millions of rows.

**Local vs cluster.** By default Spark runs `local[*]` *inside the Celery
worker* — this **is** a real Spark runtime (the bundled one) and already
partitions work across all cores. The code is master-agnostic: point
`SPARK_MASTER_URL` at a standalone cluster (see
[below](#optional-standalone-spark-cluster)) and the exact same job fans out
across executors — with `STORAGE_BACKEND=s3` every worker reads input and writes
output straight to the bucket (`s3a://`), so no shared volume is needed.

---

## LLM integration, caching & safety

`processing/llm.py` resolves a prompt to a **validated** regex:

1. **Redis cache** — keyed by a normalised hash of the prompt, model, and data
   context (target columns + sampled values), so identical requests over the
   same data are never re-sent to the LLM (`regex_source: "cache"`).
2. **LLM** (Anthropic, when `ANTHROPIC_API_KEY` is set) — constrained via
   **structured outputs** to decompose the description into per-column
   Java/Spark-compatible predicates (and, under `action: auto`, to pick the
   output action + any inline value from the prompt's verb). The prompt includes
   a few real values from each target column so the pattern matches the data's
   actual case/format (e.g. `False`, not `false`). The default model is
   **`claude-haiku-4-5`** (NL→regex is a small, latency- and cost-sensitive
   task); override with `LLM_MODEL`.
3. **Heuristic fallback** — a deterministic library so the platform runs with no
   key (`regex_source: "heuristic"`).

**Validation / ReDoS safety** (`processing/regex_safety.py`) runs on every
generated pattern before it touches Spark:

- structural sanity (non-empty, length cap),
- **compilability** under Python `re`,
- a **catastrophic-backtracking guard**: reject nested unbounded quantifiers
  (`(a+)+`-style), and run the pattern against adversarial inputs inside a hard
  wall-clock timeout (a daemon-thread probe — Celery's prefork children are
  daemonic and can't spawn processes; the static check is the primary gate and
  the probe a backstop).

Spark's `regexp_replace` uses Java regex, which can also backtrack
catastrophically — so we gate the pattern *before* it reaches the cluster. The
replacement string is escaped so user input like `$5` or a Windows path is
treated literally.

---

## Demonstrating scale (large files)

A generator is included to produce a sizeable dataset:

```bash
# 1M rows (~50 MB); use 5_000_000 for a heavier run
python scripts/generate_dataset.py --rows 1000000 --out data/uploads/big.csv
```

Then either upload `big.csv` through the UI, or drive it via the API:

```bash
# upload
UP=$(curl -s -F file=@data/uploads/big.csv http://localhost:8000/api/uploads)
ID=$(echo "$UP" | python -c "import sys,json;print(json.load(sys.stdin)['id'])")

# create a redaction job over the Email column
JOB=$(curl -s -X POST http://localhost:8000/api/jobs \
  -H 'Content-Type: application/json' \
  -d "{\"uploaded_file\":\"$ID\",\"nl_prompt\":\"find email addresses\",\"replacement_value\":\"REDACTED\",\"target_columns\":[\"Email\"]}")
JID=$(echo "$JOB" | python -c "import sys,json;print(json.load(sys.stdin)['id'])")

# poll
watch -n1 "curl -s http://localhost:8000/api/jobs/$JID | python -m json.tool | grep -E 'status|progress|stage'"
```

The web process stays responsive throughout (it only ever enqueues and polls);
all parsing and replacement happen in the Spark job inside the worker, and the
result is paged back from Parquet.

> Tune `CELERY_CONCURRENCY` and (for the cluster profile)
> `SPARK_WORKER_CORES`/`SPARK_WORKER_MEMORY` to match your hardware.

---

## Optional: standalone Spark cluster

To demonstrate true multi-executor distribution instead of local mode:

```bash
# in .env
SPARK_MASTER_URL=spark://spark-master:7077
SPARK_DRIVER_HOST=worker

docker compose --profile cluster up --build
```

This adds a Spark **master** and **worker** (Spark UI at http://localhost:8090).
The Celery worker submits jobs to the master; with `STORAGE_BACKEND=s3` every
executor reads input and writes Parquet output straight to the bucket, so no
shared volume is needed.

---

## Observability

```bash
docker compose --profile observability up
```

Adds **Flower** at http://localhost:5555 for live Celery task/worker monitoring
(queues, task states, retries, runtimes).

---

## Local development

**Iterating without rebuilds.** The base stack bakes code into the image, so
`docker compose up --build` is the right *demo* command but a slow dev loop. A
dev overlay (`docker-compose.dev.yml`) mounts your source and swaps in
auto-reloading processes — `gunicorn --reload`, Vite HMR, and a live-mounted
worker — so code edits need **no rebuild**:

```bash
docker compose -f docker-compose.yml -f docker-compose.dev.yml up
```

You only need `--build` again when *dependencies* change (`requirements.txt` /
`package.json`). See [development.md](docs/development.md) for the full loop.

### Without Docker

Requires Python 3.11, Node 20, a Java 17 JRE (for Spark), and a local Redis.
Set `DATABASE_URL` to your Neon connection string — Neon is the platform
database, required everywhere; there is no SQLite fallback. The `pytest` suite
uses it too, via an isolated `test_<db>` database (see [Tests](#tests)).

```bash
# backend
python3.11 -m venv .venv && source .venv/bin/activate
pip install -r backend/requirements.txt
cd backend
python manage.py migrate
python manage.py runserver           # http://localhost:8000

# celery worker (separate shell, same venv)
cd backend && celery -A config worker --loglevel=info

# frontend (separate shell)
cd frontend && npm install && npm run dev    # http://localhost:5173 (proxies /api)
```

---

## Tests

Unit + API tests cover the regex-safety validator, the heuristic generator, file
inspection, the storage backends, and the REST endpoints (with Celery dispatch
stubbed):

```bash
cd backend
pip install -r requirements.txt
pytest            # needs DATABASE_URL — tests run on Postgres, not SQLite
```

Tests run against the **same Postgres** as the app: Django creates an isolated
`test_<db>` database, kept between runs (`--reuse-db`, set in `pytest.ini`)
because dropping it through Neon's pooler is unreliable and slow. After a model
or migration change, refresh once with `pytest --create-db`. Storage-touching
tests are pinned to the `local` backend, so they never reach S3. The Spark engine
needs a JVM and is exercised via the Docker stack; the pure-Python layers are
covered by the suite above.

---

## Deployment

**This project is live:** the frontend is served from
[nl-regex-processor.vercel.app](https://nl-regex-processor.vercel.app) (Vercel),
and the backend stack (gunicorn + Celery/Spark worker + Redis) runs on an ARM
EC2 instance behind Vercel's server-side `/api` rewrite. Every push to `main`
runs tests, builds the arm64 image to GHCR, and deploys both halves via GitHub
Actions. The pipeline, Terraform, spend guardrails (an AWS Budget that
auto-stops the instance at 90% of the monthly limit), bot hardening, and
pause/resume scripts are documented in [docs/cicd.md](docs/cicd.md).

For any other Docker host, the generic recipe:

1. Set production env in `.env` (`DJANGO_DEBUG=False`, a strong
   `DJANGO_SECRET_KEY`, restricted `DJANGO_ALLOWED_HOSTS`, real Postgres
   credentials, and `ANTHROPIC_API_KEY`).
2. `docker compose up -d --build`.
3. Put a TLS-terminating reverse proxy in front of the `frontend` service (which
   already proxies `/api` to the backend).

For managed platforms, deploy the `backend` image as two services (web + worker)
pointing at a managed Postgres and Redis, and the `frontend` image as a static
site/container. Use `STORAGE_BACKEND=s3` so uploads and results live in the
bucket (shared by web, worker, and executors with no volume). The `local`
backend instead needs `DATA_DIR` on a filesystem shared between web and worker.

**The database is Neon.** Set `DATABASE_URL` to your
[Neon](https://neon.tech) connection string
(`postgresql://…-pooler.…neon.tech/neondb?sslmode=require&channel_binding=require`)
— `settings.py` resolves it automatically (TLS on, `channel_binding`/`options`
passed through, persistent connections), and the `web` container runs `migrate`
on start. There is no local Postgres container and no SQLite fallback; the
backend refuses to start without it. See
[deployment.md](docs/deployment.md#database-neon).

---

## Design decisions & trade-offs

- **Regex generation runs in the worker, not the request.** The brief requires
  both "return immediately with a job id" and "regex generation as a background
  task" — doing it in Celery satisfies both and keeps LLM latency off the
  request path. The cache still makes repeat prompts effectively instant.
- **Upload ingest is streamed, not queued.** The upload endpoint streams the
  file straight to object storage and reads only its header (column names + a
  small preview) before responding — the full file is never loaded into the web
  process. We deliberately keep this inline rather than dispatching a Celery
  task for it: it's bounded, near-instant work, so a task would add round-trips
  and a status-poll cycle for no gain. The *heavy, unbounded* work — regex
  generation and the Spark replacement over millions of rows — is what runs
  asynchronously.
- **Parquet + DuckDB for paged reads.** Writing Parquet lets the read path page
  efficiently without booting Spark per request; DuckDB reads only the needed
  row groups. Results are stored as Parquet; `GET /api/jobs/<id>/export` streams
  them back out as CSV or Excel on demand (the same DuckDB path, optionally
  filtered to affected rows only). Excel exports are refused past a worksheet's
  ceilings (1,048,576 rows / 16,384 columns) — the UI disables the option and
  steers to CSV, which has no such limits.
- **Spark `local[*]` by default, cluster optional.** Local mode is a real,
  partitioned Spark runtime that runs reliably on first `up`; the standalone
  cluster is one env-var + `--profile cluster` away. This favours
  reproducibility while still demonstrating horizontal scaling.
- **Neon (serverless Postgres) for job state.** A single managed database
  resolved from `DATABASE_URL` — no local Postgres container and no SQLite
  fallback anywhere. It models the production story cleanly, and persistent
  connections suit Neon's pooler. Tests run against the same Postgres too (an
  isolated `test_<db>`, reused between runs), so they exercise the real engine.
- **A dataset is never "used up".** Upload and transformation are decoupled
  (one `UploadedFile` → many `Job`s), so you can run pass after pass against the
  same dataset — the UI keeps a full, switchable run history per dataset.
- **Heuristic fallback.** Makes the system runnable and gradable with no API
  key, and doubles as a deterministic safety net. Trade-off: it covers common
  entities, not arbitrary descriptions — that's what the LLM is for.
- **Cancellation is cooperative.** A Redis flag + `cancelAllJobs` unwinds the
  job at the next checkpoint/poll rather than hard-killing the worker, leaving
  state consistent.
```
