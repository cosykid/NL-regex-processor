"""Spark.

local[*]  -> bundled Spark runtime inside the worker (default; reliable).
spark://spark-master:7077 -> standalone cluster (compose `cluster` profile).
"""
from __future__ import annotations

import os

from .env import _env_bool

SPARK_MASTER_URL = os.environ.get("SPARK_MASTER_URL", "local[*]")
SPARK_APP_NAME = os.environ.get("SPARK_APP_NAME", "nl-regex-engine")
SPARK_SHUFFLE_PARTITIONS = int(os.environ.get("SPARK_SHUFFLE_PARTITIONS", "8"))

# CSV read parallelism vs. embedded-newline correctness.
#
# ``multiLine=True`` lets a quoted field contain literal newlines, but it makes
# the CSV **non-splittable** — Spark reads the whole file in a single task on
# one core, so the read stage does NOT scale with row count (only the filter /
# write stages do). ``False`` restores size-based file splitting (one task per
# block, all cores busy) — the big win on large single-file CSVs — but a quoted
# field that spans lines would then be mis-parsed.
#
# Default ``True`` (safe for arbitrary CSV). Set ``False`` only when the data is
# known to have no newlines inside quoted fields, to regain full read
# parallelism.
SPARK_CSV_MULTILINE = _env_bool("SPARK_CSV_MULTILINE", True)

# Driver JVM heap. In the default ``local[*]`` runtime the driver *is* the
# executor, so this is the memory the cached frame lives in — bumping it reduces
# spill-to-disk on large inputs. It must be applied **before the JVM launches**
# via the ``SPARK_DRIVER_MEMORY`` env var (a ``SparkSession.config()`` set is a
# no-op once the in-process JVM is already up), which ``get_spark`` does. Empty
# = leave Spark's default (1g).
SPARK_DRIVER_MEMORY = os.environ.get("SPARK_DRIVER_MEMORY", "")

# Boot the JVM/SparkSession once when each Celery worker child starts, rather
# than lazily on its first job. The cold JVM start on ``local[*]`` is ~10-15s;
# paying it at worker boot means the user's first "apply pattern" finds a hot
# session instead of freezing on the progress bar while the JVM comes up.
SPARK_WARMUP = _env_bool("SPARK_WARMUP", True)
