# Documentation

Detailed documentation for the **Distributed NL-to-Regex Data Processing
Platform**. For a high-level overview and quick start, see the
[root README](../README.md).

## Contents

| Doc | What it covers |
|-----|----------------|
| [Architecture](architecture.md) | System components, layers, request lifecycle, sequence diagrams, design decisions |
| [Processing pipeline](pipeline.md) | NL→regex (cache/LLM/heuristic), ReDoS safety, the PySpark engine, partitioning, progress, cancellation, retries |
| [API reference](api.md) | Every REST endpoint with request/response schemas and `curl` examples |
| [Data model](data-model.md) | `UploadedFile` and `Job` fields, status lifecycle |
| [Configuration](configuration.md) | All environment variables and compose profiles |
| [Development](development.md) | Local setup (with/without Docker), tests, project layout |
| [Deployment & operations](deployment.md) | Deploying, scaling, the Spark cluster, observability, hardening |
| [Demo](demo.md) | Demo-video shot list |
| [Requirements](requirements.md) | The original assessment brief (objective + requirements) |

## At a glance

```
Upload CSV/Excel ─► describe a pattern in English ─► LLM→regex (cached)
   ─► Celery dispatches a PySpark replacement across partitions
   ─► poll live progress ─► browse paginated results
```

- **Web process never blocks** — uploads stream to object storage (S3 or a
  local volume); jobs return an id immediately; all heavy work runs in the
  Celery worker.
- **Scales to millions of rows** — native Spark column expressions across
  partitions; results written as Parquet and paged with DuckDB.
- **Runs with no API key** — a deterministic heuristic generator backs the LLM.
