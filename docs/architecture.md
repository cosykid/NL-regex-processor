# Architecture

## Goals

1. **Never block the request/response cycle.** The web process only accepts
   uploads (streamed to object storage) and enqueues/poll jobs. All parsing,
   regex generation, and replacement run asynchronously.
2. **Scale to millions of rows.** Replacement is a distributed Spark
   transformation over partitions, not a row-by-row loop.
3. **Clear separation of concerns** between the API, the task layer, and the
   data/engine layer.

## Components

| Component | Tech | Role |
|-----------|------|------|
| Frontend | React + TypeScript (Vite), served by nginx | Spreadsheet-style workspace: import a dataset, compose transformations in a formula bar, run them repeatedly, live grid + paginated results. nginx also reverse-proxies `/api`. |
| Web API | Django + DRF (gunicorn) | Accept uploads, create/poll jobs, serve paged results. Never does heavy work. |
| Broker / backend / cache | Redis | Celery broker (`/0`), result backend (`/1`), regex cache + cancel flags (`/2`). |
| Worker | Celery + PySpark | Runs the pipeline: predicate generation → action resolution → Spark select + apply (replace/mask/extract/keep/drop/find) → Parquet write. |
| Database | **Neon** (serverless Postgres) via `DATABASE_URL` | The single platform database — persists `UploadedFile` and `Job` rows (status, progress, results). No container/SQLite fallback. |
| Engine | Apache Spark (`local[*]` or standalone) | The distributed pattern-match/replace engine. |
| Object storage | S3 bucket (`STORAGE_BACKEND=s3`) or local `DATA_DIR` | `uploads/` (inputs) and `results/<job>/` (Parquet outputs), shared by web, worker, and Spark executors. |

## Layered code structure

```
backend/
├── config/            project: settings/ (env-split package), celery app, urls
├── api/               ── API LAYER ──  serializers · views/ (uploads · jobs) · exports · params · urls · pagination
├── jobs/              ── DATA LAYER ── UploadedFile + Job models
└── processing/        ── TASK + ENGINE LAYER ──
    ├── tasks.py           Celery orchestration of the pipeline
    ├── ingest.py          upload service: stage → inspect → persist → create UploadedFile
    ├── spark_engine.py    PySpark read → transform → write (+ progress, cancel)
    ├── llm.py             NL→per-column predicates: cache → LLM → heuristic fallback
    ├── regex_safety.py    validation + ReDoS guard
    ├── cache.py           Redis regex cache + cancel flags
    ├── file_inspect.py    header/preview + cursor-paged raw reads (no full-file load)
    ├── results.py         DuckDB paged reads + CSV / Excel export over Parquet (per-row match flag + stable row numbers)
    └── storage.py         pluggable object storage — local volume or S3
```

The dependency direction is one-way: `api → processing → jobs`. The API layer
knows about the task layer (to dispatch) and the data layer (to serialize); the
engine/task layer knows about the data layer; the data layer knows nothing about
the others. Request handlers stay thin: the upload flow lives in
`processing/ingest.py` and the export/streaming logic in `api/exports.py`, so the
views (split into `views/uploads.py` and `views/jobs.py`) mostly validate input
and delegate.

## High-level diagram

```
                          ┌──────────────────────────┐
        Browser  ───────► │  React SPA (nginx)        │
                          └────────────┬─────────────┘
                                       │  /api  (REST, polling)
                          ┌────────────▼─────────────┐
                          │  Django + DRF  (web)      │   ← never does heavy work
                          └─────┬───────────────┬─────┘
            enqueue task        │               │   read/write job rows
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
                  │   1. NL → predicates (cache→LLM→verify) │
                  │   2. PySpark select + replace (part.)   │
                  │   3. write Parquet result               │
                  │   + progress updates + cancellation     │
                  └──────────┬──────────────────────────────┘
                             │ read/write
                  ┌──────────▼───────────┐
                  │  Object storage      │  uploads/  results/<job>/
                  │  (S3, or local vol)  │
                  └──────────────────────┘
```

## Request lifecycle (sequence)

```
Browser        Web (Django)        Redis        Worker (Celery+Spark)       Neon
   │ POST /uploads ──►│                                                      │
   │                  │ stream to object storage, read header+preview ──────►│ create UploadedFile
   │ ◄── columns,preview ─────────────────────────────────────────────────  │
   │ POST /jobs ─────►│ create Job(QUEUED) ─────────────────────────────────►│
   │                  │ enqueue task ──►│                                    │
   │ ◄── job id (QUEUED) — returns immediately                               │
   │                  │                 │ deliver ──►│                       │
   │                  │                 │            │ RUNNING ─────────────►│
   │                  │                 │            │ predicates: cache?─►LLM─►validate
   │                  │                 │◄───────────┤ cache lookup/store    │
   │                  │                 │            │ Spark: read→select+replace→Parquet
   │                  │                 │            │ progress (poller) ───►│ progress%
   │ GET /jobs/<id> ─►│ read Job ──────────────────────────────────────────►│
   │ ◄── status,progress,predicates (poll ~1.5s)                             │
   │                  │                 │            │ SUCCESS + stats ─────►│
   │ GET /jobs/<id>/results?page= ─►│ DuckDB pages Parquet from storage ─────│
   │ ◄── {columns, rows, total, num_pages}                                   │
```

## Cancellation flow

```
Browser ── POST /jobs/<id>/cancel ──► Web
                                       ├─ set Redis cancel flag (db2)
                                       ├─ celery revoke(task_id)      (skip if not started)
                                       └─ mark Job CANCELLED (optimistic)
Worker (mid-Spark): progress poller checks the flag every 0.5s
                    └─ on flag: sparkContext.cancelAllJobs() → action aborts
                       → JobCancelled → finalize CANCELLED
```

This is **cooperative** cancellation: the running Spark action is aborted at the
next poll rather than hard-killing the worker, so state stays consistent.

## Key design decisions

- **Regex generation runs in the worker, not the request.** The brief requires
  both "return immediately with a job id" and "regex generation as a background
  task." Doing it in Celery satisfies both and keeps LLM latency off the request
  path. The Redis cache still makes repeat requests — the same prompt over the
  same column data — effectively instant.
- **Parquet + DuckDB for paged reads.** Writing Parquet lets the read path page
  efficiently (DuckDB reads only needed row groups) without booting Spark per
  request. Results are stored as Parquet and streamed back out as CSV or Excel
  on demand via `GET /jobs/<id>/export` (same DuckDB path). Excel exports are
  refused past a worksheet's ceilings (1,048,576 rows / 16,384 columns), with the
  UI steering to CSV.
- **Spark `local[*]` by default, cluster optional.** Local mode is a real,
  partitioned Spark runtime that runs reliably on first `up`; the standalone
  cluster is one env-var + `--profile cluster` away. Each Celery worker child
  warms the Spark JVM at boot (`worker_process_init`), so the ~10–15 s cold
  start is paid at startup, not on the first job's progress bar (`SPARK_WARMUP`,
  default on).
- **Neon (serverless Postgres) for job state.** A single managed database
  resolved from `DATABASE_URL` — no local Postgres container and no SQLite
  fallback. It models the production story cleanly, and persistent connections
  (`DB_CONN_MAX_AGE`) suit Neon's pooler. (The pytest suite uses the same
  Postgres: it requires `DATABASE_URL` and creates an isolated `test_<name>`
  database, reused between runs via `--reuse-db`.)
- **A dataset is transformed repeatedly.** Upload is decoupled from
  transformation: one `UploadedFile` has many `Job`s, so the UI can run pass
  after pass against the same dataset and keep a full run history.
- **Heuristic fallback (LLM).** Makes the system runnable/gradable with no API
  key and doubles as a deterministic safety net.

See [pipeline.md](pipeline.md) for the engine internals and
[data-model.md](data-model.md) for the persisted state.
