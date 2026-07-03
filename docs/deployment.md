# Deployment & operations

> **Looking for how this project is actually deployed?** The live setup —
> GitHub Actions pipeline, EC2 + Vercel topology, budget auto-stop, bot
> hardening, pause/resume scripts — is documented in [cicd.md](cicd.md).
> This doc covers deploying the images to any generic Docker host or managed
> platform.

## Deploy with docker-compose

The same images deploy to any Docker host.

1. **Configure `.env`** for production:

   ```
   DJANGO_DEBUG=False
   DJANGO_SECRET_KEY=<long random string>
   DJANGO_ALLOWED_HOSTS=your.domain.com
   DATABASE_URL=postgresql://neondb_owner:PASSWORD@ep-xxx-pooler.REGION.aws.neon.tech/neondb?sslmode=require&channel_binding=require
   ANTHROPIC_API_KEY=<key>          # optional; omit to use the heuristic generator
   CORS_ALLOW_ALL=False
   CORS_ALLOWED_ORIGINS=https://your.domain.com
   ```

2. **Bring it up:**

   ```bash
   docker compose up -d --build
   ```

3. **Terminate TLS** with a reverse proxy in front of the `frontend` service
   (which already proxies `/api` to the backend). The frontend listens on `:80`
   in-container (`:8080` on the host by default).

The web container runs migrations + `collectstatic` on start (`entrypoint.sh`),
so no manual migration step is needed.

## Topology on managed platforms

Deploy the **same backend image** as two services and the frontend separately:

| Service | Image | Command | Notes |
|---------|-------|---------|-------|
| web | backend | `web` (gunicorn) | Stateless; scale horizontally behind a load balancer. |
| worker | backend | `worker` (celery) | Runs Spark; size for CPU/memory. |
| frontend | frontend | nginx | Static SPA + `/api` proxy. |
| database | [Neon](https://neon.tech) | — | Job state. Set `DATABASE_URL` to your Neon connection string (required). |
| redis | managed | — | Broker / backend / cache. |

> Storage is `STORAGE_BACKEND=s3` in this deployment: uploads and Parquet
> results live in the bucket, so web, worker, and executors share them with no
> volume. **With the `local` backend instead**, `DATA_DIR` (`/data`) must be a
> filesystem shared between web and worker (and any Spark executors) — a shared
> mount (NFS/EFS/Filestore) in multi-host setups, or co-located web+worker.
> Compose no longer mounts a shared `/data` volume, so local mode under compose
> is single-host / non-persistent only.

### Database (Neon)

[Neon](https://neon.tech) is the platform database — there is no local Postgres
container and no SQLite fallback. Point the app at Neon by setting

    DATABASE_URL=postgresql://neondb_owner:PASSWORD@ep-xxx-pooler.REGION.aws.neon.tech/neondb?sslmode=require&channel_binding=require

(use the *pooled* connection string from the Neon dashboard — paste it verbatim;
the database name and query params are read from the URL). `settings.py`
resolves it automatically — TLS (`sslmode=require`) is applied by default,
`channel_binding`/`options`/`connect_timeout` are passed through, and
connections persist via `DB_CONN_MAX_AGE`. Run the `web` and `worker` images
with `DATABASE_URL` set; the `web` container runs `migrate` on start, creating
the schema in Neon. If `DATABASE_URL`/`NEON_DATABASE_URL` is missing the backend
fails fast with a clear configuration error. No code or image changes are
required.

## Scaling

- **More throughput:** raise `CELERY_CONCURRENCY` and/or run more worker
  replicas. Each worker process runs its own Spark driver, so size memory
  accordingly. With `SPARK_WARMUP` on (default), every child boots its JVM at
  worker start — expect `CELERY_CONCURRENCY` JVMs warming up front, and a few
  extra seconds before the worker reports ready.
- **Bigger jobs:** tune `spark.sql.files.maxPartitionBytes` (smaller = more
  splits = more parallelism, more task overhead) and, on the cluster profile,
  `SPARK_WORKER_CORES` / `SPARK_WORKER_MEMORY`.
- **Web tier:** raise `GUNICORN_WORKERS` or add web replicas (stateless).

## Standalone Spark cluster

To run replacement across dedicated executors instead of local mode:

```bash
# .env
SPARK_MASTER_URL=spark://spark-master:7077
SPARK_DRIVER_HOST=worker

docker compose --profile cluster up -d --build
```

This adds a Spark master + worker (UI at http://localhost:8090). The Celery
worker submits jobs to the master; with `STORAGE_BACKEND=s3` every executor
reads input and writes Parquet output straight to the bucket (`s3a://`), so no
shared volume is needed. Match the Spark version (`apache/spark:3.5.1`) to the
`pyspark` version in `requirements.txt`.

## Observability

```bash
docker compose --profile observability up -d
```

Flower at http://localhost:5555 shows live Celery queues, task states, retries,
and runtimes. Beyond that:

- **Health:** `GET /healthz` (used by the backend container healthcheck).
- **Logs:** structured stdout from web + worker (`docker compose logs -f`).
- **Job state:** query the API (`GET /api/jobs`, filterable by `?uploaded_file=`)
  or Neon directly; each job row carries `stage`, `progress`, `error_message`,
  and the resolved regex. (There is no Django admin site — this is a token-less
  API with `admin`/`auth` not installed.)
- **Spark UI:** available per-application while a job runs (cluster profile
  master UI on :8090).

## Production hardening checklist

- [ ] Strong `DJANGO_SECRET_KEY`; `DJANGO_DEBUG=False`; restricted
      `DJANGO_ALLOWED_HOSTS`.
- [ ] `DATABASE_URL` points at your Neon project; Neon's backups/retention configured.
- [ ] `ANTHROPIC_API_KEY` (and all secrets) supplied via the platform's secret
      store / `.env` — **never** in `.env.example` or committed files.
- [ ] TLS termination in front of the frontend; lock down CORS.
- [ ] Redis with auth/network isolation (it holds the queue + cache).
- [ ] Durable object storage — `STORAGE_BACKEND=s3` with the bucket + IAM from
      `infra/terraform` (or, for the `local` backend, shared durable `/data`).
- [ ] Resource limits on workers (each runs a Spark JVM).
- [ ] Backups / retention policy for `results/` Parquet if outputs must persist.

## Backups & retention

- **Job metadata** lives in Neon — rely on Neon's backups / point-in-time restore.
- **Uploads and results** live in the S3 bucket under `uploads/` and
  `results/<job>/` (or under `DATA_DIR` with the `local` backend). They can grow;
  add a retention/cleanup policy (e.g. an S3 lifecycle rule) as needed.
