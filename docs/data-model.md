# Data model

Two persisted entities (`backend/jobs/models.py`), both keyed by UUID. They form
the data layer; the API and task layers read/write them but the models depend on
neither.

## UploadedFile

One uploaded file plus its lightweight header inspection.

| Field | Type | Notes |
|-------|------|-------|
| `id` | UUID (pk) | |
| `original_name` | char(512) | Original filename |
| `kind` | enum | `csv` \| `excel` |
| `path` | char(1024) | Storage locator — an S3 key (`uploads/<id>.<ext>`) or a local path, per `STORAGE_BACKEND` |
| `size_bytes` | bigint | File size |
| `columns` | JSON | Header column names |
| `created_at` | datetime | |

> The row **preview is not stored**. It's computed by the header inspection and
> returned inline in the upload response (`preview_rows`), then re-read from
> object storage on demand — the file is the source of truth, so raw sample rows
> never sit in the metadata DB. Only `columns` is persisted.

A file may have many jobs (`Job.uploaded_file` FK, `related_name="jobs"`).

## Job

One natural-language replacement request against an uploaded file.

**Request**

| Field | Type | Notes |
|-------|------|-------|
| `id` | UUID (pk) | |
| `uploaded_file` | FK → UploadedFile | `on_delete=CASCADE` |
| `nl_prompt` | text | The natural-language pattern description |
| `replacement_value` | text | Blank = delete matches |
| `target_columns` | JSON | Columns to apply the replacement to |

**Lifecycle**

| Field | Type | Notes |
|-------|------|-------|
| `status` | enum | `QUEUED` · `RUNNING` · `SUCCESS` · `FAILED` · `CANCELLED` |
| `progress` | smallint | 0–100 |
| `stage` | char(64) | Human-readable current step |
| `celery_task_id` | char(255) | For revoke/inspection |
| `error_message` | text | Populated on `FAILED` |

**Resolved regex**

| Field | Type | Notes |
|-------|------|-------|
| `regex_pattern` | text | The applied pattern |
| `regex_source` | enum | `cache` · `llm` · `heuristic` |
| `regex_explanation` | text | One-line explanation |

**Result**

| Field | Type | Notes |
|-------|------|-------|
| `result_path` | char(1024) | Storage locator for the Parquet result dir — S3 key (`results/<id>/`) or local path |
| `total_rows` | bigint \| null | Row count |
| `matched_rows` | bigint \| null | Rows with at least one match in a target column |
| `result_columns` | JSON | Column order of the result |
| `created_at` / `updated_at` | datetime | |

`is_terminal` is `True` when `status ∈ {SUCCESS, FAILED, CANCELLED}`.

## Status lifecycle

```
                      ┌─────────────► SUCCESS
QUEUED ──► RUNNING ───┼─────────────► FAILED
   │          │       └─────────────► CANCELLED
   └──────────┴───────────────────► CANCELLED  (cancel before/while running)
```

- `QUEUED` — created, task enqueued, nothing started.
- `RUNNING` — the worker picked it up (regex generation → Spark).
- `SUCCESS` — Parquet result written; `total_rows`/`matched_rows` set.
- `FAILED` — permanent error or transient retries exhausted; see `error_message`.
- `CANCELLED` — user requested cancellation; the Spark action was aborted.

## Deletion & storage cleanup

Deleting a row also removes its bytes from object storage, via `post_delete`
signals (`backend/jobs/signals.py`): deleting an `UploadedFile` removes its
stored file, and deleting a `Job` removes its Parquet result directory. Because
the FK is `on_delete=CASCADE`, deleting an `UploadedFile` cascades to its `Job`s
and each job's result is cleaned up too. Cleanup is best-effort — a storage
error is logged, never blocking the database delete.

> This is a token-less JSON API: there is **no** Django admin site or auth. The
> `admin`, `auth`, `sessions`, and `contenttypes` apps are not installed, so
> their tables don't exist. Inspect jobs via the API (`GET /api/jobs`) or Neon
> directly.
