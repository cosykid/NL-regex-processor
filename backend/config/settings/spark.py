"""Spark.

local[*]  -> bundled Spark runtime inside the worker (default; reliable).
spark://spark-master:7077 -> standalone cluster (compose `cluster` profile).
"""
from __future__ import annotations

import os

SPARK_MASTER_URL = os.environ.get("SPARK_MASTER_URL", "local[*]")
SPARK_APP_NAME = os.environ.get("SPARK_APP_NAME", "nl-regex-engine")
# Target rows-per-partition; the engine derives a partition count from this so
# work fans out across cores/executors and progress reporting is granular.
SPARK_ROWS_PER_PARTITION = int(os.environ.get("SPARK_ROWS_PER_PARTITION", "200000"))
SPARK_SHUFFLE_PARTITIONS = int(os.environ.get("SPARK_SHUFFLE_PARTITIONS", "8"))
