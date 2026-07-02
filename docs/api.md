# API reference

Base URL: `/api` (e.g. `http://localhost:8000/api`, or `http://localhost:8080/api`
through the frontend proxy). All bodies are JSON unless noted. IDs are UUIDs.

| Method | Path | Purpose |
|--------|------|---------|
| `POST` | `/api/uploads` | Upload a CSV/Excel file (multipart) |
| `GET`  | `/api/uploads/{id}` | Upload metadata |
| `GET`  | `/api/uploads/{id}/rows` | Scroll the original file — a cursor-paged window of raw rows |
| `POST` | `/api/jobs` | Create a job (dispatch async work) |
| `GET`  | `/api/jobs` | List jobs (paginated; filter by `?uploaded_file=`) |
| `GET`  | `/api/jobs/{id}` | Poll a job's status/progress |
| `POST` | `/api/jobs/{id}/cancel` | Request cancellation |
| `GET`  | `/api/jobs/{id}/results` | Paged processed result (optionally affected rows only) |
| `GET`  | `/api/jobs/{id}/export` | Download the processed result (CSV or Excel via `?fmt=`) |
| `GET`  | `/healthz` | Liveness check (not under `/api`) |

---

## POST /api/uploads

Multipart upload. The file is streamed to object storage in chunks (staged to a
local temp file, then persisted to S3 or the local volume — never buffered in the
web process); only the header + a small preview are read.

**Request** — `multipart/form-data` with field `file`.

```bash
curl -F file=@samples/contacts.csv http://localhost:8000/api/uploads
```

**201 Created**

```json
{
  "id": "0f728241-eb66-4fbb-a717-71203de3f634",
  "original_name": "contacts.csv",
  "kind": "csv",
  "size_bytes": 184,
  "columns": ["ID", "Name", "Email"],
  "preview_rows": [
    {"ID": "1", "Name": "John Doe", "Email": "john.doe@example.com"}
  ],
  "created_at": "2026-06-30T11:21:57.755180Z"
}
```

| Field | Type | Notes |
|-------|------|-------|
| `kind` | `csv` \| `excel` | Detected from the filename |
| `columns` | string[] | Header row |
| `preview_rows` | object[] | First `UPLOAD_PREVIEW_ROWS` rows (default 20) |

> `preview_rows` is computed at upload and returned here **inline** — it is not
> persisted, so `GET /api/uploads/{id}` omits it. Use `/rows` to page the file.

**400** — no `file` field, an unparseable file, or no header row.

---

## GET /api/uploads/{id}

Returns the upload metadata (`id`, `original_name`, `kind`, `size_bytes`,
`columns`, `created_at`) — the same as the create response but **without**
`preview_rows` (that's computed inline only at upload; use `/rows` to read the
file). **404** if not found.

---

## GET /api/uploads/{id}/rows

A window of the **raw uploaded file**, so the UI can lazily scroll the whole
dataset before any transformation is applied (not just the small preview from
the upload response). Reads straight from the CSV/Excel in object storage (a
ranged read from S3, or a local file) — no Spark, no full-file load.

Continuation is **cursor-based**, which is what keeps deep scrolling cheap: each
response returns an opaque `cursor` that the next request passes back, so a
sequential scroll never re-scans rows it already read. For CSV the cursor is a
byte offset (resuming is a `seek`, so every fetch is O(window) regardless of
depth); streaming `.xlsx` can't be byte-seeked, so its cursor is the next row
index. Omit the cursor for the first window.

**Query:**

| Param | Default | Notes |
|-------|---------|-------|
| `cursor` | _(none)_ | Opaque token from the previous response. Omit for the first window. |
| `limit` | `100` | Rows per window, capped at 500. |

```bash
curl "http://localhost:8000/api/uploads/<id>/rows?limit=100"                 # first window
curl "http://localhost:8000/api/uploads/<id>/rows?cursor=<cursor>&limit=100" # next window
```

**200**

```json
{
  "rows": [
    {"ID": "1", "Name": "John Doe", "Email": "john.doe@example.com"}
  ],
  "eof": false,
  "cursor": "4096",
  "limit": 100
}
```

| Field | Notes |
|-------|-------|
| `rows` | Raw rows (objects keyed by column). No `__matched__` / `__rownum` — this is the untransformed file. |
| `eof` | `true` once the end of the file is reached; the client stops requesting more. No full-file row count is ever computed. |
| `cursor` | Token for the next window, or `null` at EOF. |

**400** — a non-integer `limit`, or an invalid/malformed `cursor`.

**410** — the uploaded file is no longer in storage.

---

## POST /api/jobs

Creates a job, enqueues the async pipeline, and **returns immediately** with a
job id (status `QUEUED`). Does not block on the LLM or Spark.

**Request**

```json
{
  "uploaded_file": "0f728241-eb66-4fbb-a717-71203de3f634",
  "nl_prompt": "Find email addresses",
  "replacement_value": "REDACTED",
  "target_columns": ["Email"]
}
```

| Field | Type | Required | Notes |
|-------|------|----------|-------|
| `uploaded_file` | uuid | yes | An existing upload id |
| `nl_prompt` | string | yes | Natural-language description of the pattern |
| `replacement_value` | string | no | Defaults to `""` (blank = delete matches) |
| `target_columns` | string[] | yes | Must be a subset of the upload's columns |

```bash
curl -X POST http://localhost:8000/api/jobs -H 'Content-Type: application/json' \
  -d '{"uploaded_file":"<id>","nl_prompt":"Find email addresses","replacement_value":"REDACTED","target_columns":["Email"]}'
```

**201 Created** — a [Job object](#job-object) with `status: "QUEUED"`.

**400** — validation error, e.g. a target column not in the file:

```json
{"target_columns": "Column(s) not in the uploaded file: Foo. Available: Email, ID, Name"}
```

---

## GET /api/jobs

Paginated list of jobs (most recent first). DRF page-number pagination.

**Query:** `page`, `page_size` (default 50, max 500), and `uploaded_file`
(a dataset id) to list only that dataset's runs. A dataset can be transformed
any number of times, so the workspace uses this to show a dataset's full run
history:

```bash
curl "http://localhost:8000/api/jobs?uploaded_file=<upload-id>&page_size=100"
```

```json
{
  "count": 5,
  "next": null,
  "previous": null,
  "results": [ /* Job objects */ ]
}
```

---

## GET /api/jobs/{id}

Poll a job. Returns a [Job object](#job-object). Poll this (~1.5s) until
`status` is terminal (`SUCCESS` / `FAILED` / `CANCELLED`).

---

## POST /api/jobs/{id}/cancel

Requests cancellation of a `QUEUED`/`RUNNING` job: sets a Redis flag (so a
running Spark action aborts at the next checkpoint), revokes the Celery task,
and reflects `CANCELLED` immediately.

- **200** — Job object (now `CANCELLED` / `cancelling`).
- **409** — the job is already terminal:

```json
{"detail": "Job already SUCCESS.", "job": { /* Job object */ }}
```

---

## GET /api/jobs/{id}/results

Paged view of the processed result (read from Parquet via DuckDB).

**Query:**

| Param | Default | Notes |
|-------|---------|-------|
| `page` | `1` | 1-based page index |
| `page_size` | `50` | capped at 500 |
| `matched_only` | `false` | `true`/`1`/`yes` → return **only affected rows** (rows the pattern matched). Pagination then runs over the affected subset. |

```bash
curl "http://localhost:8000/api/jobs/<id>/results?page=1&page_size=50"
curl "http://localhost:8000/api/jobs/<id>/results?page=1&matched_only=true"
```

**200**

```json
{
  "columns": ["ID", "Name", "Email"],
  "rows": [
    {"ID": "1", "Name": "John Doe", "Email": "REDACTED", "__matched__": true, "__rownum": 1}
  ],
  "total": 1000000,
  "total_all": 1000000,
  "matched_total": 814233,
  "has_match_flag": true,
  "matched_only": false,
  "page": 1,
  "page_size": 50,
  "num_pages": 20000
}
```

| Field | Notes |
|-------|-------|
| `columns` | display columns (the internal match-flag column is hidden) |
| `rows[].__matched__` | per-row flag: did the pattern match this row? (`true` even when the replacement didn't change the text) |
| `rows[].__rownum` | the row's **original 1-based index** in the full result — preserved under `matched_only`, so an affected row keeps its full-view number instead of being renumbered. Assigned by DuckDB `row_number()` over a single-threaded (deterministic) scan. |
| `total` | rows in the current view (the affected subset when `matched_only`) |
| `total_all` | total rows in the result, ignoring the filter |
| `matched_total` | number of affected rows |
| `has_match_flag` | `false` for results written before this flag existed (then `__matched__` is `false` and `matched_only` is ignored) |

**409** — the result is not ready (job is not `SUCCESS`):

```json
{"detail": "Result not available (job is RUNNING)."}
```

---

## GET /api/jobs/{id}/export

Streams the processed result as a download (built from Parquet via DuckDB, so
even a million-row result never loads into memory). The internal match-flag
column is excluded.

**Query:**
- `fmt` — `csv` (default) or `xlsx`. (Named `fmt`, not `format`: DRF reserves
  `?format=` for content negotiation and 404s on values it has no renderer for.)
- `matched_only` (`true`/`1`/`yes`) → export only the affected rows.

```bash
curl -OJ "http://localhost:8000/api/jobs/<id>/export"                   # all rows, CSV
curl -OJ "http://localhost:8000/api/jobs/<id>/export?fmt=xlsx"          # all rows, Excel
curl -OJ "http://localhost:8000/api/jobs/<id>/export?matched_only=true" # affected only
```

**200** — `Content-Disposition: attachment; filename="<source-stem>.<ext>"` (the
affected-only export appends `-affected`). Content type is `text/csv` for CSV and
`application/vnd.openxmlformats-officedocument.spreadsheetml.sheet` for Excel.

**400** — an unknown `fmt`, or an `xlsx` export whose size exceeds a worksheet's
limits (1,048,576 rows or 16,384 columns); the response `detail` points to CSV.
The UI also disables the Excel option up front in this case, so the request is
normally never made.

**409** — the result is not ready (job is not `SUCCESS`).

---

## Job object

Returned by the job create / detail / list / cancel endpoints.

```json
{
  "id": "9d85b93b-5893-4d4d-bff4-74e7b7ee5c02",
  "uploaded_file": {
    "id": "0f728241-...",
    "original_name": "contacts.csv",
    "columns": ["ID", "Name", "Email"]
  },
  "nl_prompt": "Find email addresses",
  "replacement_value": "REDACTED",
  "target_columns": ["Email"],
  "status": "SUCCESS",
  "progress": 100,
  "stage": "completed",
  "regex_pattern": "\\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\\.[A-Za-z]{2,7}\\b",
  "regex_source": "llm",
  "regex_explanation": "Matches email addresses.",
  "total_rows": 1000000,
  "matched_rows": 1000000,
  "result_columns": ["ID", "Name", "Email"],
  "error_message": "",
  "created_at": "2026-06-30T11:21:57Z",
  "updated_at": "2026-06-30T11:22:01Z"
}
```

| Field | Type | Notes |
|-------|------|-------|
| `status` | enum | `QUEUED` · `RUNNING` · `SUCCESS` · `FAILED` · `CANCELLED` |
| `progress` | int | 0–100 |
| `stage` | string | Human-readable current step (e.g. `applying replacement (Spark write)`) |
| `regex_source` | enum | `cache` · `llm` · `heuristic` (empty until resolved) |
| `total_rows` / `matched_rows` | int \| null | Populated on success |
| `error_message` | string | Populated on `FAILED` |

See [data-model.md](data-model.md) for the full field list and lifecycle.

---

## Error format

DRF default. Validation errors are field-keyed (`{"field": ["msg"]}` or
`{"field": "msg"}`); other errors use `{"detail": "..."}`. Relevant codes:
`201` create, `200` ok, `400` validation, `404` not found, `409` conflict
(result not ready / already terminal).
