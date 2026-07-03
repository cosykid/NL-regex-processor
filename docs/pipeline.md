# Processing pipeline

The full pipeline runs inside the Celery task `processing.process_job`
(`backend/processing/tasks.py`). The web process only enqueues it.

```
QUEUED ─► RUNNING ─► (1) NL→predicates ─► (2) Spark select + replace ─► SUCCESS
                          │                        │
                          └─ permanent error ──────┴─► FAILED
                          └─ cancel flag ─────────────► CANCELLED
                          └─ transient error ─────────► retry (backoff) ─► … ─► FAILED
```

Progress is written to both the `Job` row (read by the polling API) and the
Celery task state.

---

## 1. Natural language → per-column predicates

`processing/llm.py` resolves a prompt to a **validated** set of match
*predicates* in three tiers. A predicate is one `{column, pattern}`; the
predicates combine with a `combinator` (`all` = AND, `any` = OR) to decide
whether a row is selected. "name starts with A **and** phone starts with 0"
becomes two predicates joined by `all`; a single-condition prompt over one
column is a one-element list, so this subsumes the earlier single-pattern model.

1. **Redis cache** (`processing/cache.py`) — keyed by a normalised SHA-256 of
   the prompt, model, **and data context** (the target columns + the sampled
   cell values fed to the model). Identical requests over the same data never
   re-hit the LLM. The heuristic is data-independent, so it keeps a prompt-only
   key. → `source: "cache"`.
2. **LLM** (Anthropic) when `ANTHROPIC_API_KEY` is set — constrained via
   **structured outputs** (`output_config.format`, a JSON schema of
   `{combinator, predicates[], explanation}` — plus `action` and `value` on the
   `auto` path, where the model also picks the output action) to **decompose**
   the description into per-column Java/Spark-compatible predicates. The prompt
   includes a few real sample values from each target column so patterns match
   the data's actual case/format. Default model `claude-haiku-4-5` (override
   with `LLM_MODEL`). → `source: "llm"`.
3. **Heuristic fallback** otherwise — a deterministic keyword library so the
   platform runs with no key. It maps the whole prompt to one entity pattern and
   fans it across every target column with `any` (it can't decompose a compound
   cross-column condition without the LLM). A quoted literal is anchored per any
   positional phrasing ("starts with 'Dr'" → `^Dr`, "exactly 'x'" → `^x$`), and
   a deterministic verb scan mirrors the LLM's action choice so `auto` still
   resolves sensibly (e.g. "mask …" → `mask`, "drop rows …" → `drop`).
   → `source: "heuristic"`.

### LLM prompt contract

The system prompt tells the model to map the description of which **rows** to
select into one predicate per column-condition — each `column` must be one of
the given target columns — and to pick `combinator` from the language ("and" →
`all`, "or" → `any`). Patterns are constrained to `java.util.regex` syntax
(Spark uses the Java engine): character classes, quantifiers, anchors,
`\b \d \w \s`, non-capturing groups — and forbid possessive quantifiers,
variable-length lookbehind, and nested unbounded quantifiers. Positional phrases
map to anchors ("starts with X" → `^X`, "ends with X" → `X$`). The response is
parsed as JSON; a predicate naming a column outside the target set, or an
unparseable/empty result, is surfaced as a clear job error.

**Output action.** Every job carries an `action` — what to do with the matches:
`replace` / `mask` / `extract` rewrite matched cells, `keep` / `drop` filter
rows, `find` only reports, and `auto` (the default) defers the choice to the
model. The system prompt is specialised accordingly: under `auto` it carries the
verb→action menu (and the model returns `action` + any inline `value` it read
from the prompt); under an explicit action it instead carries guidance on what a
match *means* for that transformation (an `extract` pattern isolates a
substring; a `keep`/`drop` pattern is a whole-row test worth anchoring), so the
regex is shaped for the action rather than generated blind to it. The requested
action is folded into the cache key — the same words under `extract` vs
`replace` must not collide. `tasks._resolve_action` then picks what actually
runs: an explicit request wins outright; on `auto` the model's action is used
(defaulting to `replace`), and a replacement value the user typed always beats
one the model pulled from the prompt. The outcome is persisted as
`Job.resolved_action` so the UI can show "Auto → Mask".

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
| quoted literal | `'cat'`, "starts with 'Dr'" | `\b<escaped>\b`; positional phrasing anchors it (`^Dr`, `Dr$`, `^Dr$`) |
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

### Session warm-up

`get_spark()` is `getOrCreate`, so the JVM/SparkSession boots on first use and
is then reused for the process's lifetime. On `local[*]` that cold boot is
~10–15 s — paid lazily, it would land mid-job and freeze the progress bar on the
first "apply pattern" after every worker start. So each Celery prefork child
warms Spark at boot: the `worker_process_init` signal (`config/celery.py`) calls
`warm_spark()`, which does `get_spark(...)` + a trivial `range(1).count()` to
force full JVM/SQL/codegen init. The first real job then finds a hot session.
Warm-up is best-effort (a failure is logged, not fatal — the job falls back to
lazy creation), skipped in eager mode (tests), and gated by
[`SPARK_WARMUP`](configuration.md) (default on). `run_replacement` also sets a
`starting Spark engine` stage before `get_spark()` so a cold session (warm-up
off, or cluster mode) still shows a live stage rather than a stuck bar.

### Read → transform → write

1. **Read** — CSV is read natively by Spark, directly from storage (an
   `s3a://` URI on S3, or a local path). Excel is first stream-converted to CSV
   with openpyxl's read-only iterator (Excel is inherently bounded, ~1M rows).
2. **Cache + scan** — the frame is cached (`MEMORY_AND_DISK`) and a **single**
   pass computes `total_rows` **and** `matched_rows` in one `agg`
   (`count` + `sum(when(match_condition,1))`). Each predicate is a native
   `col.rlike(pattern)` expression; `build_match_condition` combines them with
   `&` (`all`) or `|` (`any`). The same condition becomes the internal
   `__nlrx_matched__` flag on every written row. See
   [partitioning](#partitioning).
3. **Transform** — the resolved action is applied to the selection, only ever
   touching matched rows; everything is native JVM column expressions (no
   Python row UDFs):
   - **Cell actions** (`replace` / `mask` / `extract`) rewrite each predicate's
     column with its own pattern, wrapped in `when(matched, …).otherwise(col)`
     so only matched rows change — a compound `A AND B` condition never edits
     rows that don't satisfy the whole condition. `replace` substitutes the
     match (blank deletes it), `mask` overwrites it with the mask token
     (default `••••`), and `extract` collapses the cell to just the matched
     text (`regexp_extract`, additionally guarded by that column's own `rlike`
     so an OR row never blanks a column with no match of its own).
   - **Row actions** (`keep` / `drop`) filter the dataset to the matched rows
     or their complement; no cell is edited.
   - **`find`** passes the data through unchanged — the match flag column and
     the row counts are the entire result.
4. **Write** — result written as **Parquet** to the job's result locator
   (`s3a://…/results/<job_id>/` on S3, or under `DATA_DIR` locally); the cached
   frame is then unpersisted.

### Partitioning

Partitioning is left to Spark's native file splitting: an uncompressed CSV is
split by size (`spark.sql.files.maxPartitionBytes`, default 128 MB) into one
task per split, so a large file fans out across cores (local) or executors
(cluster) with no extra step. We do **not** `repartition` — a forced full
shuffle dominated cost on small files and was redundant on large ones already
split.

The source is read/parsed **once** and cached (`MEMORY_AND_DISK`): the
row-count pass materialises the cache and the write reads from it, so the CSV is
never re-parsed per action (previously three passes: count, matched-count,
write).

### Progress

A background `_ProgressPoller` thread polls
`SparkContext.statusTracker()` for completed/total tasks during each action and
maps the fraction onto a progress band (start Spark 14% → read 15% →
scan 42% → write 95% → finalise 98% → done 100%).

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
