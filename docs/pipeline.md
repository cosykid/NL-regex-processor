# Processing pipeline

The full pipeline runs inside the Celery task `processing.process_job`
(`backend/processing/tasks.py`). The web process only enqueues it.

```
QUEUED ─► RUNNING ─► (1) NL→regex ─► (2) Spark replacement ─► SUCCESS
                          │                    │
                          └─ permanent error ──┴─► FAILED
                          └─ cancel flag ─────────► CANCELLED
                          └─ transient error ─────► retry (backoff) ─► … ─► FAILED
```

Progress is written to both the `Job` row (read by the polling API) and the
Celery task state.

---

## 1. Natural language → regex

`processing/llm.py` resolves a prompt to a **validated** regex in three tiers:

1. **Redis cache** (`processing/cache.py`) — keyed by a normalised SHA-256 of
   the prompt, model, **and data context** (the target columns + the sampled
   cell values fed to the model). Identical requests over the same data never
   re-hit the LLM. The heuristic is data-independent, so it keeps a prompt-only
   key. → `source: "cache"`.
2. **LLM** (Anthropic) when `ANTHROPIC_API_KEY` is set — constrained via
   **structured outputs** (`output_config.format`, a JSON schema of
   `{pattern, explanation}`) to emit a single Java/Spark-compatible regex. The
   prompt includes a few real sample values from each target column so the
   pattern matches the data's actual case/format. Default model
   `claude-haiku-4-5` (override with `LLM_MODEL`). → `source: "llm"`.
3. **Heuristic fallback** otherwise — a deterministic keyword library so the
   platform runs with no key. → `source: "heuristic"`.

### LLM prompt contract

The system prompt constrains the model to `java.util.regex` syntax (Spark's
`regexp_replace` uses the Java engine): character classes, quantifiers, anchors,
`\b \d \w \s`, non-capturing groups — and forbids possessive quantifiers,
variable-length lookbehind, and nested unbounded quantifiers. The response is
parsed as JSON; failures are surfaced as a clear job error.

**Data samples.** The user message also carries a few real values from each
target column — e.g. `- Railway: "False", "True"` — and the system prompt tells
the model to treat them as ground truth for the data's case, spelling, and
formatting (so "remove False" over `False`/`True` yields `False`, not a
lower-cased `false`) unless the description explicitly asks to normalise or
ignore case. Samples come from `file_inspect.sample_values` over a small preview
the worker re-reads from object storage at job start (a cheap ranged read of the
first rows — the preview is no longer persisted on the `UploadedFile`), taking
up to `LLM_SAMPLE_VALUES_PER_COLUMN` distinct values per column
(each capped at `LLM_SAMPLE_VALUE_MAXLEN` chars) **spread across** the preview
window rather than clustered on the first rows. Because the samples steer the
output, they are folded into the cache key (tier 1 above): the same words over
different data resolve to different regexes instead of the first result being
cached and wrongly reused.

### Heuristic coverage

Resolution order is **specific entities → quoted literal → generic catch-alls**,
so `"replace the word 'cat'"` matches the literal, not the generic `word` rule.

| Category | Examples of triggers | Pattern (abridged) |
|----------|---------------------|--------------------|
| email | "email" | `\b[A-Za-z0-9._%+-]+@…\.[A-Za-z]{2,7}\b` |
| url | "url", "link", "website" | `https?://[^\s]+` |
| ipv4 | "ip address" | `\b(?:\d{1,3}\.){3}\d{1,3}\b` |
| ssn / credit card / zip | "ssn", "card number", "zip" | `\d{3}-\d{2}-\d{4}` etc. |
| phone | "phone", "mobile" | `\+?\d{0,3}[\s.-]?…` |
| date / time / currency | "date", "time", "price" | `\d{4}-\d{2}-\d{2}` etc. |
| hashtag / mention | "hashtag", "mention" | `#\w+`, `@\w+` |
| quoted literal | `'cat'`, `"foo"` | `\b<escaped>\b` |
| generic | "number", "word", "whitespace" | `\d+`, `\b\w+\b`, `\s+` |

If nothing matches, the job fails with a message telling the user to set a key
or describe a known entity.

---

## Regex safety (ReDoS guard)

`processing/regex_safety.py` validates **every** generated pattern before it
touches Spark, because Spark's Java engine can also backtrack catastrophically:

1. **Structural** — non-empty, ≤ 2000 chars.
2. **Compilability** — must compile under Python `re`.
3. **Static danger heuristic** — reject nested unbounded quantifiers
   (`(a+)+`-style), the classic exponential shape.
4. **Timed probe** — run the pattern against short adversarial inputs inside a
   **1s wall-clock timeout in a daemon thread**. If it doesn't finish, refuse.

> The probe uses a *thread*, not a process: Celery's prefork workers are
> daemonic, and Python forbids daemonic processes from spawning children. A
> thread can't be force-killed, but the static check already rejects the
> exponential shapes — this layer is a backstop.

The replacement string is escaped (`escape_replacement`) so user input like
`$5` or a Windows path is treated literally by Java's `regexp_replace` (which
otherwise interprets `$` and `\`).

---

## 2. PySpark replacement engine

`processing/spark_engine.py`.

### Read → transform → write

1. **Read** — CSV is read natively by Spark, directly from storage (an
   `s3a://` URI on S3, or a local path). Excel is first stream-converted to CSV
   with openpyxl's read-only iterator (Excel is inherently bounded, ~1M rows).
2. **Count** — `df.count()` establishes the row count (for partitioning + stats).
3. **Repartition** — see [partitioning](#partitioning).
4. **Match scan** — `matched_rows` = rows where any target column `rlike`s the
   pattern.
5. **Transform** — `regexp_replace(col, pattern, replacement)` applied to each
   target column as a native JVM column expression (no Python row UDFs).
6. **Write** — result written as **Parquet** to the job's result locator
   (`s3a://…/results/<job_id>/` on S3, or under `DATA_DIR` locally).

### Partitioning

The partition count is derived from the row count and a configurable target
rows-per-partition (`SPARK_ROWS_PER_PARTITION`, default 200k):

```
partitions = ceil(total_rows / SPARK_ROWS_PER_PARTITION)   # capped at 1024
```

Rationale:

- **Bounded task working set** — predictable memory per task.
- **Horizontal parallelism** — partition count ≈ task count, so work spreads
  across all cores (local) or executors (cluster). 5M rows → ~25 tasks.
- **Granular progress** — more tasks → smoother progress reporting.

### Progress

A background `_ProgressPoller` thread polls
`SparkContext.statusTracker()` for completed/total tasks during each action and
maps the fraction onto a progress band (read 15% → count 28% → scan 42% →
write 95% → finalise 98% → done 100%).

### Cancellation

The same poller checks the Redis cancel flag every 0.5s; on cancel it calls
`sparkContext.cancelAllJobs()`, which aborts the in-flight action. The task
catches this and finalises the job as `CANCELLED`.

---

## 3. Paged result reads

`processing/results.py` serves result pages from the web process with **DuckDB**:

```sql
-- glob resolves to s3://…/results/<job>/*.parquet or a local path
SELECT * FROM read_parquet('<result>/*.parquet') LIMIT ? OFFSET ?
```

DuckDB reads only the Parquet row groups needed for the page, so paging a
million-row result is cheap and never boots Spark. On S3 it reads over `httpfs`
(same AWS credential chain); `page_size` is capped at 500.

---

## Failure handling & retries

| Failure | Class | Behaviour |
|---------|-------|-----------|
| LLM network / 5xx, Redis hiccup | `TransientError` | Celery retry with exponential backoff (`min(60, 2^retries)`s), up to 3 times; then `FAILED` |
| Unknown column, unsafe regex, Spark error, no heuristic match | permanent (`ProcessingError` subclasses) | `FAILED` immediately (no retry) |
| User cancellation | `JobCancelled` | `CANCELLED` |

All progress/state transitions are written via lightweight
`Job.objects.filter(...).update(...)` calls so concurrent writers don't clobber
each other.
