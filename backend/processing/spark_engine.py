"""Distributed pattern-matching & replacement engine (PySpark).

The engine reads the uploaded file into a Spark DataFrame, applies the
LLM-generated regex as a native ``regexp_replace`` transformation across the
target column(s), and writes the result back as Parquet for paged reading.

Design notes
------------
* **Native column expressions, not row UDFs.** ``regexp_replace`` / ``rlike``
  run inside the JVM across partitions — no per-row Python round-trips — so the
  job scales horizontally as row count grows into the millions.
* **Partitioning.** We size the partition count from ``SPARK_ROWS_PER_PARTITION``
  (target rows per task). That keeps each task's working set bounded and gives
  the progress poller enough tasks to report smooth, granular progress.
* **Progress.** A background thread polls ``SparkContext.statusTracker()`` for
  task-completion fraction during the (long-pole) write and maps it onto a
  progress band surfaced through the polling API.
* **Cancellation.** The same thread checks a cancel callback and calls
  ``cancelAllJobs()``, which aborts the in-flight Spark action.

``pyspark`` is imported lazily so the rest of the Django app (and the
pure-Python tests) import without a Spark/JVM install present.
"""
from __future__ import annotations

import csv
import logging
import threading
import time
from dataclasses import dataclass, field
from typing import Callable

from django.conf import settings

from . import storage
from .exceptions import JobCancelled, SparkProcessingError
from .regex_safety import escape_replacement

logger = logging.getLogger("processing")

ProgressCb = Callable[[int, str], None]
CancelCb = Callable[[], bool]

# Internal boolean column appended to the written result: True for every row
# where at least one target column matched the pattern — regardless of whether
# the replacement actually changed the text. Lets the UI emphasise affected
# rows, offer an affected-only view, and scope an export. Prefixed/suffixed to
# avoid colliding with a real column from the source file; stripped from every
# user-facing column list and export.
MATCH_FLAG_COLUMN = "__nlrx_matched__"


@dataclass
class ReplacementStats:
    total_rows: int = 0
    matched_rows: int = 0
    columns: list[str] = field(default_factory=list)
    result_path: str = ""


def get_spark(job_id: str):
    """Get/create the SparkSession for this worker process."""
    from pyspark.sql import SparkSession  # type: ignore[import-not-found]

    builder = (
        SparkSession.builder.appName(f"{settings.SPARK_APP_NAME}-{job_id}")
        .master(settings.SPARK_MASTER_URL)
        .config("spark.sql.shuffle.partitions", settings.SPARK_SHUFFLE_PARTITIONS)
        .config("spark.ui.showConsoleProgress", "false")
    )
    # S3A filesystem + credential-provider config when the storage backend is
    # S3 (empty dict for local, so this is a no-op there).
    for key, value in storage.spark_hadoop_conf().items():
        builder = builder.config(key, value)

    import os

    driver_host = os.environ.get("SPARK_DRIVER_HOST")
    if driver_host:
        builder = builder.config("spark.driver.host", driver_host)
    return builder.getOrCreate()


def _prepare_input(input_locator: str, kind: str) -> str:
    """Resolve the Spark-readable URI for the source file.

    CSV is read in place — a local path or an ``s3a://`` URI, straight from the
    storage backend. Excel isn't Spark-native, so it's localized (a no-op for
    the local backend, a download for S3) and streamed to a temp CSV that Spark
    reads from the local filesystem. Excel is inherently bounded (~1M rows), so
    streaming it with openpyxl's read-only iterator is cheap; the million-row
    distributed scaling target is the CSV path.

    NOTE: the Excel branch writes a *local* temp CSV, which the default
    ``local[*]`` runtime reads directly. Under a standalone cluster
    (``spark://``) remote executors wouldn't see it — Excel there would need the
    converted CSV staged back to shared storage first.
    """
    from jobs.models import UploadedFile

    if kind != UploadedFile.Kind.EXCEL:
        return storage.spark_read_uri(input_locator)

    from openpyxl import load_workbook

    src = storage.localize(input_locator, suffix=".xlsx")
    out = src.with_suffix(src.suffix + ".converted.csv")
    if out.exists():
        return str(out)

    wb = load_workbook(filename=str(src), read_only=True, data_only=True)
    try:
        ws = wb.active
        if ws is None:
            raise SparkProcessingError(
                f"Excel file has no active worksheet: {input_locator}"
            )
        with open(out, "w", newline="", encoding="utf-8") as fh:
            writer = csv.writer(fh)
            for row in ws.iter_rows(values_only=True):
                writer.writerow(["" if v is None else v for v in row])
    finally:
        wb.close()
    return str(out)


class _ProgressPoller(threading.Thread):
    """Polls Spark task progress and the cancel flag during an action."""

    def __init__(self, spark, band: tuple[int, int], stage_label: str,
                 progress_cb: ProgressCb, cancel_cb: CancelCb):
        super().__init__(daemon=True)
        self._sc = spark.sparkContext
        self._start, self._end = band
        self._label = stage_label
        self._progress_cb = progress_cb
        self._cancel_cb = cancel_cb
        # NB: not `self._stop` — that name shadows Thread._stop(), the private
        # method CPython calls during join(), which would blow up with
        # "'Event' object is not callable".
        self._stop_event = threading.Event()
        self.cancelled = False

    def run(self) -> None:  # pragma: no cover - timing/JVM dependent
        tracker = self._sc.statusTracker()
        while not self._stop_event.wait(0.5):
            if self._cancel_cb():
                self.cancelled = True
                self._sc.cancelAllJobs()
                return
            try:
                total = completed = 0
                for sid in tracker.getActiveStageIds():
                    info = tracker.getStageInfo(sid)
                    if info:
                        total += info.numTasks
                        completed += info.numActiveTasks  # in-flight as partial
                        completed += info.numCompletedTasks
                if total:
                    frac = min(completed / total, 1.0)
                    pct = int(self._start + frac * (self._end - self._start))
                    self._progress_cb(pct, self._label)
            except Exception:  # noqa: BLE001 - progress is best-effort
                pass

    def stop(self) -> None:
        self._stop_event.set()


def _run_action(spark, band, label, progress_cb, cancel_cb, action):
    """Run a Spark action with live progress polling + cancellation."""
    poller = _ProgressPoller(spark, band, label, progress_cb, cancel_cb)
    poller.start()
    try:
        return action()
    except Exception as exc:  # noqa: BLE001
        if poller.cancelled or cancel_cb():
            raise JobCancelled("Job cancelled during Spark processing") from exc
        raise
    finally:
        poller.stop()
        poller.join(timeout=2)
        if poller.cancelled:
            raise JobCancelled("Job cancelled during Spark processing")


def run_replacement(
    *,
    job_id: str,
    input_locator: str,
    kind: str,
    regex_pattern: str,
    replacement_value: str,
    target_columns: list[str],
    result_locator: str,
    progress_cb: ProgressCb,
    cancel_cb: CancelCb,
) -> ReplacementStats:
    """Apply ``regex_pattern`` to ``target_columns`` and write Parquet results."""
    from pyspark.sql import functions as F  # type: ignore[import-not-found]

    spark = get_spark(job_id)
    progress_cb(15, "reading file into Spark")

    read_uri = _prepare_input(input_locator, kind)
    df = (
        spark.read.option("header", True)
        .option("multiLine", True)
        .option("escape", '"')
        .csv(read_uri)
    )
    all_columns = df.columns
    missing = [c for c in target_columns if c not in all_columns]
    if missing:
        raise SparkProcessingError(
            f"Target column(s) not found in file: {', '.join(missing)}"
        )

    if cancel_cb():
        raise JobCancelled("Job cancelled before processing")

    # Size partitions from the configured target rows-per-partition.
    total_rows = _run_action(
        spark, (15, 28), "counting rows", progress_cb, cancel_cb, df.count
    )
    partitions = max(1, min(1024, -(-total_rows // settings.SPARK_ROWS_PER_PARTITION)))
    df = df.repartition(partitions)

    # Rows where at least one target column matches the pattern.
    match_condition = None
    for col in target_columns:
        cond = F.col(col).rlike(regex_pattern)
        match_condition = cond if match_condition is None else (match_condition | cond)

    matched_rows = _run_action(
        spark, (28, 42), "scanning for matches", progress_cb, cancel_cb,
        lambda: df.filter(match_condition).count(),
    )

    # Tag each row with whether it matched, computed from the ORIGINAL values
    # (before the replacement rewrites the target columns). Adding this column
    # first means the later replacement projections leave it intact.
    transformed = df.withColumn(MATCH_FLAG_COLUMN, match_condition)

    # Apply the replacement across the target columns.
    replacement = escape_replacement(replacement_value)
    for col in target_columns:
        transformed = transformed.withColumn(
            col, F.regexp_replace(F.col(col), regex_pattern, replacement)
        )

    write_uri = storage.spark_write_uri(result_locator)
    _run_action(
        spark, (42, 95), "applying replacement (Spark write)", progress_cb, cancel_cb,
        lambda: transformed.write.mode("overwrite").parquet(write_uri),
    )

    progress_cb(98, "finalising")
    return ReplacementStats(
        total_rows=total_rows,
        matched_rows=matched_rows,
        columns=all_columns,
        result_path=result_locator,
    )
