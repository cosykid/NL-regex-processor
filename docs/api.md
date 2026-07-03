# API reference

Base URL: `/api` (e.g. `http://localhost:8000/api`, or `http://localhost:8080/api`
through the frontend proxy). All bodies are JSON unless noted. IDs are UUIDs.

| Method | Path | Purpose |
|--------|------|---------|
| `POST` | `/api/uploads` | Upload a CSV/Excel file (multipart POST through the API) |
| `POST` | `/api/uploads/presign` | Start a direct-to-storage upload (S3 backend) |
| `POST` | `/api/uploads/complete` | Finalize a single-PUT direct upload |
| `POST` | `/api/uploads/multipart/create` | Open a parallel multipart upload (large files) |
| `POST` | `/api/uploads/multipart/complete` | Finalize a multipart upload |
| `POST` | `/api/uploads/multipart/abort` | Cancel an in-progress multipart upload |
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

## Direct-to-storage uploads

On the **S3 backend** the browser uploads straight to the bucket via presigned
URLs — the file's bytes never pass through the web process. The server only
decides the storage key + kind (the client echoes back an opaque `id`) and later
reads the header. On the **local backend** there's no browser-reachable target,
so `presign` returns `{"mode": "direct"}` and the client falls back to
`POST /api/uploads`.

Two shapes, chosen by size:

- **Small files** — one presigned `PUT` (`presign` → `PUT` → `complete`).
- **Large files** (client threshold **16 MB**) — a **parallel multipart** upload
  (`multipart/create` → parallel part `PUT`s → `multipart/complete`). One `PUT`
  can't saturate a high-latency uplink (a single TCP stream is capped by its
  bandwidth-delay product; the bucket is in `ap-southeast-2`/Sydney), so the file
  is sliced and the parts are uploaded concurrently (a pool of ~6) to lift total
  throughput.

Both paths need bucket CORS to allow `PUT`; multipart additionally needs the
`ETag` response header **CORS-exposed** (each part's ETag is read client-side and
handed to `complete`). `infra/terraform/s3.tf` sets `allowed_methods = [GET, PUT,
HEAD]` and `expose_headers = ["ETag"]`.

**Throughput characteristics** — the multipart win is not universal; it's a
bandwidth-delay-product effect, so it only shows under specific conditions:

- **High-RTT links only.** Single-stream throughput ≈ `TCP_window / RTT`. When
  the round-trip is large (e.g. a client in the US/EU → the Sydney bucket,
  ~150–250 ms) one `PUT` can't fill the pipe and the parallel parts lift total
  throughput ~2–5×. A client **near** the bucket (low RTT, e.g. within Australia)
  already saturates the uplink with a single stream, so multipart there is
  expected to be ~1× — working as designed, not a regression.
- **Needs HTTP/1.1.** The parallel parts only get *independent* TCP congestion
  windows over HTTP/1.1 (the browser opens up to ~6 sockets per host). S3's REST
  endpoint is HTTP/1.1. Put a CloudFront/ALB in front that negotiates HTTP/2 and
  the parts multiplex over one connection = one congestion window = the BDP cap
  returns and the gain disappears. Check the Protocol column in DevTools.
- **Request count = `ceil(size / part_size)`.** Each part is its own `PUT`, so a
  large file shows many same-named rows in the Network panel (they differ only by
  the `?partNumber=` query). That's the mechanism, not a leak — see the part-size
  formula under `multipart/create`.

### POST /api/uploads/presign

**Request** — `{"filename": "contacts.csv"}`.

**200** — S3 backend: `{"mode": "s3", "id": "<uuid>", "url": "<presigned PUT>"}`.
Local backend: `{"mode": "direct"}` (fall back to `POST /api/uploads`).

### POST /api/uploads/complete

Finalizes a single-PUT direct upload: looks up the pending record stashed at
presign time (so the client can't dictate the key or kind), reads the header, and
records it.

**Request** — `{"id": "<uuid from presign>"}`.
**201** — same body as `POST /api/uploads`.
**410** — the upload session expired or the id is unknown.

### POST /api/uploads/multipart/create

Opens a multipart upload and returns a presigned URL per part. The server picks
the part size — `part_size = max(5 MB, ceil(size / 64))`, capped at 10000 parts
(S3's limits: every part but the last is ≥ 5 MB, ≤ 10000 parts). The client
slices the file into `part_size` chunks and PUTs each to its `url` in parallel.

**Request** — `{"filename": "big.csv", "size": 734003200}` (`size` in bytes).

**200**

```json
{
  "id": "0f728241-eb66-4fbb-a717-71203de3f634",
  "part_size": 11468800,
  "parts": [
    {"part_number": 1, "url": "https://<bucket>.s3.../uploads/<id>.csv?partNumber=1&uploadId=..."},
    {"part_number": 2, "url": "https://..."}
  ]
}
```

| Field | Notes |
|-------|-------|
| `id` | Opaque upload id; echo it back to `complete`/`abort`. |
| `part_size` | Bytes per part. Part `n` (1-based) covers `[(n-1)·part_size, …)`. |
| `parts` | One `{part_number, url}` per part, ascending. |

**400** — missing `filename`, or a missing/non-positive `size`.

### POST /api/uploads/multipart/complete

Assembles the uploaded parts into the final object (from the server-held S3
upload id — never the client's), reads the header, and records it. Send back each
part's `ETag` (read from the part `PUT` response). Order doesn't matter; the
server sorts by `part_number`.

**Request**

```json
{
  "id": "0f728241-...",
  "parts": [
    {"part_number": 1, "etag": "\"e1a...\""},
    {"part_number": 2, "etag": "\"9c4...\""}
  ]
}
```

**201** — same body as `POST /api/uploads`.
**400** — an invalid/empty `parts` payload, or the assembled file is unparseable
/ over the size limit.
**410** — the upload session expired or the id is unknown.

### POST /api/uploads/multipart/abort

Discards an in-progress multipart upload so already-uploaded parts don't linger
(a bucket lifecycle rule is the backstop). The client calls this on cancel or an
unrecoverable part failure.

**Request** — `{"id": "<uuid>"}`.
**204** — aborted (or a no-op for an unknown/already-cleared id; idempotent).

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
| `replacement_value` | string | no | Only read by `replace` (blank = delete matches) and `mask` (blank = default `••••` token). Defaults to `""` |
| `target_columns` | string[] | yes | Must be a subset of the upload's columns |
| `action` | string | no | `auto` (default), `find`, `replace`, `mask`, `extract`, `keep`, `drop`. `auto` infers the action (and any value) from the prompt; `find` only counts/flags matches — the data passes through unchanged |

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
| `rows[].__rownum` | the row's **original 1-based index** in the full result — preserved under `matched_only`, so an affected row keeps its full-view number instead of being renumbered. Ordering is deterministic via the Parquet scan position (file name, then row within file): the full view numbers rows positionally from the page offset, the affected-only view numbers every row first and then filters. |
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
  "action": "auto",
  "resolved_action": "replace",
  "status": "SUCCESS",
  "progress": 100,
  "stage": "completed",
  "predicates": [
    {
      "column": "Email",
      "pattern": "\\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\\.[A-Za-z]{2,7}\\b",
      "explanation": "Matches email addresses."
    }
  ],
  "combinator": "all",
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

The request shape is unchanged — one `nl_prompt`, `target_columns`, and an
optional `replacement_value`. A cross-column description like `"name starts with
A and phone starts with 0"` over `target_columns: ["name", "phone"]` resolves to
two `predicates` with `combinator: "all"` (AND); use "or" in the prompt for
`"any"`.

| Field | Type | Notes |
|-------|------|-------|
| `status` | enum | `QUEUED` · `RUNNING` · `SUCCESS` · `FAILED` · `CANCELLED` |
| `progress` | int | 0–100 |
| `stage` | string | Human-readable current step (e.g. `applying replace (Spark write)`) |
| `action` | enum | The requested output action (`auto` · `find` · `replace` · `mask` · `extract` · `keep` · `drop`) |
| `resolved_action` | enum | The concrete action that actually ran — what `auto` resolved to (never `auto`; empty until the conditions are resolved) |
| `predicates` | array | Per-column match conditions `{column, pattern, explanation}` (empty until resolved) |
| `combinator` | enum | `all` (AND) · `any` (OR) |
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
