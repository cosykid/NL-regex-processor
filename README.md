# NL-to-Regex Data Processing Platform

This app lets you upload a CSV or Excel file, then describe a pattern in plain English ("find email
addresses") to replace every match across the dataset. An LLM turns the
description into a regex, and the replacement runs as a distributed Apache
Spark job dispatched through Celery.

```
"Find email addresses in the Email column and replace them with 'REDACTED'."
        │
        ▼   LLM (cached in Redis)
\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,7}\b
        │
        ▼   Spark regexp_replace across partitions
   john.doe@example.com  ->  REDACTED
```

**Stack:** Django + DRF · Celery · Redis · PySpark · React + TypeScript ·
Neon (serverless Postgres) · S3 or local volume · Docker Compose

## Demo video

**[Watch the demo on youtube](https://youtu.be/7rFbCCqzX2g)**

## Notes & tradeoffs

- **Heavily used Claude Code!**
- **Regex generation runs in the worker, not the request.** The specification required
  both "return a job id immediately" and "regex generation as a background
  task", and Celery satisfies both. However, even a fully cached prompt goes
  through the queue and a poll cycle before the UI shows anything, so the
  happy path is slightly slower than it strictly needs to be.
- **Progress is polled, not pushed.** The UI asks for job status every 1.5
  seconds instead of holding a WebSocket. Polling is boring,
  proxy friendly, and needed no new infrastructure, but it wastes requests on
  long jobs and caps how live the progress bar can feel.
- **Upload ingest is streamed inline, not queued.** It is quick to reader a header and queueing it would add a second poll cycle for no
  gain. We just have to hope that header inspection stays cheap, but a malformed file is
  the risk, which is why only the first bytes are ever read.
- **`docker compose up` is not fully self-contained.** Used Neon instead of
  a Postgres container because it's cheap and easy to use out of the box.
- **Spark runs local[*] by default.** In local mode Spark spreads work across the CPU cores in the Celery worker container rather than separate machines. So the "distributed" processing is really just threads on one box. Kept it simple for this assessment. The Spark code stays the same either way, so if you later point the master URL at a real cluster you get true multi-machine distribution and the job itself doesn't change.
- **The backend is just one EC2 instance running compose.** It is cheap, at least for an AWS hosted deployment. AWS is familiar to me and also aligns with the tech stack of Rhombus AI so why not use it until I run out of my free $200 USD credits. But there is no high availability, and Redis lives unmanaged on the
  same host, so if the box dies the queue and cache die with it. Job rows
  survive in Neon and files in S3, so nothing is lost except the jobs that
  were running.
- **The API is public with no auth.** We have a host filter, an IP rate limit in
  Redis, and threaded gunicorn workers keep casual bots away from the LLM and
  Spark, but a patient abuser could still burn LLM credit while staying under
  the limits. The automatic AWS budget stop is my only hope. This was for the sake of keeping the demo easy to use without requiring auth.
- **Result pages are cached client-side.** The frontend keeps a bounded LRU of viewed pages (60-entry cap), which means paging back to a run you already looked at is served from memory with no refetch and no revalidation logic to get wrong.
- **A handful of real cell values from each target column go into the LLM prompt**, so the model writes regex against the column's true case and format (False, not false) instead of guessing. The sample is capped per column and per value length to keep token cost down, and it reads only the preview rows already captured at upload, never the full file.

## Quick start

You need Docker, and one setting: the database is
[Neon](https://neon.tech), so paste your _pooled_ connection string into
`.env` as `DATABASE_URL` (there's no local Postgres container).

```bash
git clone <your-repo-url> nl-regex-processor
cd nl-regex-processor
cp .env.example .env    # set DATABASE_URL to your Neon connection string

docker compose up --build
```

That brings up Redis, the Django API, the Celery worker with a bundled Spark
runtime, and the React frontend at **http://localhost:8080** (API at
http://localhost:8000/api).

No Claude API key? No problem. Without `ANTHROPIC_API_KEY`, a deterministic
heuristic generator covers common patterns (emails, phones, URLs, dates, and
so on), so the whole pipeline runs anyways. Set the key in `.env` and the LLM
handles arbitrary descriptions.

To try the example from the brief: upload a CSV with an `Email` column, select
it, enter _"Find email addresses"_ with replacement `REDACTED`, and run.

## Architecture

The **API** (Django/DRF) only accepts work and answers
polls; the **task layer** (Celery + Redis) owns everything slow; the **engine**
(PySpark) does the actual data transformation.

```
Browser ──► React SPA ──► Django + DRF ── enqueue ──► Redis ──► Celery worker
                              │                                  1. NL → regex
                              │ job rows                            (cache → LLM → validate)
                              ▼                                  2. Spark replacement
                          Neon (PG)                              3. write Parquet
                                                                       │
                              object storage (local volume or S3) ◄───┘
```

A job flows through like this:

1. **Upload.** The file streams straight to object storage. Only the header
   and a small preview are read to populate the column picker, so even an
   upload of several gigabytes is inspected cheaply.
2. **Create job.** `POST /api/jobs` persists a `Job` (`QUEUED`), enqueues a
   Celery task, and returns a job id immediately. It never waits on the LLM or
   Spark.
3. **Worker pipeline.** The worker resolves the prompt into validated regex
   predicates for each column (Redis cache first, then LLM or heuristic),
   applies them as a partitioned Spark transformation, and writes the result
   as Parquet. Progress is mirrored to the `Job` row and the Celery task state.
4. **Poll and results.** The UI polls `GET /api/jobs/<id>` for status and
   progress, then pages through results with DuckDB, which reads only the
   Parquet row groups it needs. We don't send millions of rows to the browser
   at once.

Failures are split into transient (LLM or network errors, which Celery retries
with backoff) and permanent (an unknown column or unsafe regex, which fails
immediately). Cancellation is cooperative: a Redis flag that the task and the
Spark poller both observe, unwinding cleanly to `CANCELLED`.

Full detail lives in [`docs/`](docs/README.md): [architecture](docs/architecture.md) ·
[pipeline](docs/pipeline.md) · [API reference](docs/api.md) ·
[data model](docs/data-model.md) · [configuration](docs/configuration.md) ·
[development](docs/development.md) · [deployment](docs/deployment.md) ·
[CI/CD](docs/cicd.md)

## The Spark engine, and why it's partitioned the way it is

The regex is applied as native column expressions (`regexp_replace`, `rlike`)
rather than Python UDFs. The work stays inside the JVM across partitions,
without serializing every row through Python, which is what keeps throughput
flat as rows grow into the millions.

Partitioning is left to Spark's native file splitting
(`spark.sql.files.maxPartitionBytes`, 128 MB default): a large CSV splits by
size into one task per split, spreading across all cores or executors with no
extra step. I deliberately don't `repartition`. A forced full shuffle
dominated cost on small files and was redundant on large ones. The source is
parsed once and cached (`MEMORY_AND_DISK`), so counting and writing don't
re-parse the file.

By default Spark runs `local[*]` inside the Celery worker, which is a real,
partitioned Spark runtime that works on the first `docker compose up`. The
code doesn't care where the master lives: point `SPARK_MASTER_URL` at a
standalone cluster and the same job fans out across executors:

```bash
# in .env: SPARK_MASTER_URL=spark://spark-master:7077, SPARK_DRIVER_HOST=worker
docker compose --profile cluster up --build   # Spark UI at :8090
```

## LLM integration & regex safety

The deployed application uses the Claude Haiku 4.5 API.

Prompt resolution checks the Redis cache first (keyed by prompt, model, and
data context), then calls the LLM, and falls back to the heuristic generator
when no key is set. The LLM call uses Anthropic structured outputs and is fed
sample values from the target columns, so patterns match the data's actual
format. Cached prompts never hit the LLM twice.

Every generated pattern is validated before it touches Spark
(`processing/regex_safety.py`): it must compile, pass a check for
catastrophic backtracking (nested unbounded quantifiers are rejected, and a
timed probe runs the pattern against adversarial inputs), and the replacement
string is escaped so input like `$5` stays literal. Spark's Java regex can
backtrack catastrophically too, which is why patterns are gated before
dispatch, not after.

## Demonstrating scale

```bash
# generate 1M rows (~50 MB); bump --rows for a heavier run
python scripts/generate_dataset.py --rows 1000000 --out data/uploads/big.csv
```

Upload it through the UI (or with `curl`; see [docs/api.md](docs/api.md)) and
run a redaction over the `Email` column. The web process stays responsive
throughout; all parsing and replacement happen in the Spark job inside the
worker.

## Tests & local development

```bash
cd backend && pip install -r requirements.txt
pytest    # needs DATABASE_URL: tests run on Postgres (isolated test_<db>)
```

The suite covers the regex safety validator, the heuristic generator, file
inspection, the storage backends, and the REST endpoints (with Celery dispatch
stubbed). The Spark engine needs a JVM and is exercised through the Docker
stack.

For a fast dev loop without image rebuilds (mounted source, hot reload):

```bash
docker compose -f docker-compose.yml -f docker-compose.dev.yml up
```

See [docs/development.md](docs/development.md) for the setup without Docker.

## Deployment

The app is live: the frontend on Vercel
([nl-regex-processor.vercel.app](https://nl-regex-processor.vercel.app)), the
backend (gunicorn plus the Celery/Spark worker and Redis) on an ARM EC2
instance behind Vercel's `/api` rewrite. Every push to `main` runs tests,
builds the arm64 image, and deploys both halves via GitHub Actions. The
pipeline, Terraform, and spend guardrails are documented in
[docs/cicd.md](docs/cicd.md); recipes for any other Docker host or managed
platform are in [docs/deployment.md](docs/deployment.md).
