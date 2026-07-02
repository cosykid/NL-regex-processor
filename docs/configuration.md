# Configuration

All configuration is environment-driven (`backend/config/settings.py`). With
docker-compose, values come from `.env` (copy from `.env.example`) and the
`x-backend-env` anchor in `docker-compose.yml`.

> **Secrets:** put real secrets (e.g. `ANTHROPIC_API_KEY`) in `.env`, which is
> gitignored. Keep `.env.example` as placeholders only — it is committed.

## LLM

| Variable | Default | Description |
|----------|---------|-------------|
| `ANTHROPIC_API_KEY` | _(empty)_ | When set, NL→regex uses the LLM; when empty, the deterministic heuristic fallback is used. |
| `LLM_MODEL` | `claude-haiku-4-5` | Model for NL→regex. Override for tougher prompts (e.g. `claude-sonnet-4-6`). |
| `LLM_MAX_TOKENS` | `1024` | Max output tokens for the regex generation call. |
| `LLM_SAMPLE_VALUES_PER_COLUMN` | `10` | Distinct sample values per target column shown to the LLM (drawn spread across the preview window) so patterns match the data's real case/format. |
| `LLM_SAMPLE_VALUE_MAXLEN` | `80` | Max characters per sample value; longer cells are truncated before being shown to the model. |
| `REGEX_CACHE_TTL` | `2592000` (30d) | TTL (seconds) for cached regexes in Redis. |

> The LLM cache key includes the sampled data context (target columns + values),
> not just the prompt — so "remove False" over `False`/`True` data and over
> `FALSE` data cache separately. The heuristic path is data-independent and stays
> keyed on the prompt alone. See [pipeline.md](pipeline.md#1-natural-language--regex).

## Django

| Variable | Default | Description |
|----------|---------|-------------|
| `DJANGO_SECRET_KEY` | `dev-insecure-change-me` | **Set a strong value in production.** |
| `DJANGO_DEBUG` | `True` (compose sets `False`) | Debug mode. |
| `DJANGO_ALLOWED_HOSTS` | `*` | Comma-separated allowed hosts. |
| `CORS_ALLOW_ALL` | `True` | Allow all CORS origins (dev). |
| `CORS_ALLOWED_ORIGINS` | _(empty)_ | Comma-separated explicit origins when `CORS_ALLOW_ALL=False`. |
| `LOG_LEVEL` | `INFO` | Root log level. |

## Storage

Uploaded files and Spark results live in one of two backends, chosen by
`STORAGE_BACKEND`. Only *metadata* (columns, a small preview, result stats) is
ever stored in Neon; the file bytes and Parquet output go to the backend below.

| Variable | Default | Description |
|----------|---------|-------------|
| `STORAGE_BACKEND` | `local` | `local` (files under `DATA_DIR`) or `s3` (an S3 bucket). |
| `DATA_DIR` | `<repo>/data` (compose: `/data`) | **local backend** root; `uploads/` and `results/` live under it. Created on start only in local mode; compose no longer mounts a shared volume here, so under compose it is container-local (non-persistent). |
| `UPLOAD_PREVIEW_ROWS` | `20` | Rows read for the upload preview. |
| `MAX_UPLOAD_BYTES` | `2147483648` (2 GB) | Upper bound advertised for uploads (streamed in chunks regardless, never buffered in memory). |

### S3 backend (`STORAGE_BACKEND=s3`)

Provision the bucket + IAM with `infra/terraform` (see its README), then
`terraform output dotenv_snippet` for the exact values.

| Variable | Default | Description |
|----------|---------|-------------|
| `S3_BUCKET` | _(**required** when `s3`)_ | Bucket for `uploads/` and `results/`. The backend refuses to start without it. |
| `S3_REGION` | `ap-southeast-2` | Bucket region (falls back to `AWS_REGION` / `AWS_DEFAULT_REGION`). |
| `S3_ENDPOINT_URL` | _(empty)_ | S3-compatible endpoint for MinIO / LocalStack; empty = real AWS S3. |
| `AWS_ACCESS_KEY_ID` / `AWS_SECRET_ACCESS_KEY` | _(empty)_ | Static keys for **local** dev. Leave blank when deployed — the app resolves an IAM role via the default AWS credential chain instead. |
| `AWS_SESSION_TOKEN` | _(empty)_ | Set when using temporary/STS credentials. |

Auth is identical across `boto3` (uploads), Spark S3A (distributed read/write),
and DuckDB `httpfs` (result paging/export): all resolve the **default AWS
credential provider chain**, so the same image runs locally with keys and on
AWS with a role — no code change. The S3A jars are baked into the backend image
(`hadoop-aws` + `aws-java-sdk-bundle`, pinned to Spark 3.5.1 / Hadoop 3.3.4).

## Database (Neon)

[Neon](https://neon.tech) is the single platform database. The app resolves one
connection from **`DATABASE_URL`** (or **`NEON_DATABASE_URL`**) — there is **no**
local Postgres container and **no** SQLite fallback. If neither variable is set,
the backend refuses to start with a clear `ImproperlyConfigured` error. The test
suite runs against the **same** Postgres — Django creates and drops an isolated
`test_<db>` database each run — so tests exercise the real engine (no SQLite).

| Variable | Default | Description |
|----------|---------|-------------|
| `DATABASE_URL` / `NEON_DATABASE_URL` | _(**required**)_ | Full Neon/Postgres connection URL — paste Neon's verbatim. Example: `postgresql://neondb_owner:PASSWORD@ep-xxx-pooler.REGION.aws.neon.tech/neondb?sslmode=require&channel_binding=require`. The **database name is taken from the URL path** (Neon's default project DB is `neondb`). TLS (`sslmode=require`) is applied by default; URL-encoded credentials are decoded; and the libpq params Neon emits (`channel_binding`, `options=endpoint%3D…`, `connect_timeout`) are passed straight through. |
| `DB_CONN_MAX_AGE` | `600` | Seconds to keep a DB connection alive (persistent connections — good for serverless Postgres). |

> Get the URL from the Neon dashboard → your project → **Connection Details**
> (use the *pooled* connection string — it has `-pooler` in the host). Copy it
> as-is; no need to edit the database name or strip query params. The `web`
> container runs `migrate` against it on start and creates the schema in Neon.

## Redis / Celery

| Variable | Default | Description |
|----------|---------|-------------|
| `REDIS_URL` | `redis://localhost:6379` (compose: `redis://redis:6379`) | Base URL; the three roles use separate DBs. |
| `CELERY_BROKER_URL` | `${REDIS_URL}/0` | Task queue. |
| `CELERY_RESULT_BACKEND` | `${REDIS_URL}/1` | Task state + return values. |
| `REDIS_CACHE_URL` | `${REDIS_URL}/2` | Regex cache + cancel flags. |
| `CELERY_CONCURRENCY` | `2` | Worker process count (`--concurrency`). |
| `CELERY_LOGLEVEL` | `info` | Worker log level. |
| `CELERY_TASK_ALWAYS_EAGER` | `False` | Run tasks inline (testing only). |
| `GUNICORN_WORKERS` | `3` | Web worker processes. |

## Spark

| Variable | Default | Description |
|----------|---------|-------------|
| `SPARK_MASTER_URL` | `local[*]` | `local[*]` (bundled runtime) or `spark://spark-master:7077` (cluster profile). |
| `SPARK_DRIVER_HOST` | _(unset)_ | Set to `worker` when using the cluster profile so executors can reach the driver. |
| `SPARK_ROWS_PER_PARTITION` | `200000` | Target rows per partition (drives the partition count). |
| `SPARK_SHUFFLE_PARTITIONS` | `8` | `spark.sql.shuffle.partitions`. |
| `SPARK_APP_NAME` | `nl-regex-engine` | Spark application name prefix. |
| `SPARK_WORKER_CORES` | `2` | (cluster profile) cores for the standalone Spark worker. |
| `SPARK_WORKER_MEMORY` | `2g` | (cluster profile) memory for the standalone Spark worker. |

## Compose profiles

| Command | Adds |
|---------|------|
| `docker compose up` | Core: redis, backend, worker, frontend (database is Neon, via `DATABASE_URL`) |
| `docker compose --profile cluster up` | Standalone Spark master + worker (Spark UI on :8090) |
| `docker compose --profile observability up` | Flower (Celery monitoring) on :5555 |

### Ports

| Service | Host port |
|---------|-----------|
| frontend (nginx) | `8080` |
| backend (API) | `8000` |
| Flower | `5555` (observability profile) |
| Spark master UI | `8090` (cluster profile) |

To use the cluster profile, set in `.env`:

```
SPARK_MASTER_URL=spark://spark-master:7077
SPARK_DRIVER_HOST=worker
```
