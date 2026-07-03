"""Object storage backend for uploads + Spark results.

  local (default) -> files under DATA_DIR (below). No AWS needed; used by tests.
  s3              -> an S3 bucket (provisioned by infra/terraform). Auth uses
                     the default AWS credential chain: static keys locally,
                     an IAM role when deployed — no code change between them.
"""
from __future__ import annotations

import os
from pathlib import Path

from django.core.exceptions import ImproperlyConfigured

from .env import BASE_DIR

STORAGE_BACKEND = os.environ.get("STORAGE_BACKEND", "local").strip().lower()

# Local-backend storage roots (uploads + Spark results). Always defined — the
# local backend and the test suite reference these paths — but only materialized
# on disk in local mode; in s3 mode nothing writes here, so we don't create it.
DATA_DIR = Path(os.environ.get("DATA_DIR", BASE_DIR.parent / "data"))
UPLOAD_DIR = DATA_DIR / "uploads"
RESULTS_DIR = DATA_DIR / "results"
if STORAGE_BACKEND == "local":
    for _d in (DATA_DIR, UPLOAD_DIR, RESULTS_DIR):
        _d.mkdir(parents=True, exist_ok=True)
    # Spill Django's large-upload temp file onto the same volume as the final
    # upload dir, so ingest can *rename* it into place instead of copying every
    # byte across filesystems (the OS default temp dir is usually a different fs).
    FILE_UPLOAD_TEMP_DIR = str(UPLOAD_DIR)
S3_BUCKET = os.environ.get("S3_BUCKET", "")
S3_REGION = (
    os.environ.get("S3_REGION")
    or os.environ.get("AWS_REGION")
    or os.environ.get("AWS_DEFAULT_REGION")
    or "ap-southeast-2"
)
# Optional S3-compatible endpoint (MinIO / LocalStack). Empty = real AWS S3.
S3_ENDPOINT_URL = os.environ.get("S3_ENDPOINT_URL", "")

if STORAGE_BACKEND not in {"local", "s3"}:
    raise ImproperlyConfigured(
        f"STORAGE_BACKEND must be 'local' or 's3', got '{STORAGE_BACKEND}'."
    )
if STORAGE_BACKEND == "s3" and not S3_BUCKET:
    raise ImproperlyConfigured(
        "STORAGE_BACKEND=s3 requires S3_BUCKET. Provision it with "
        "infra/terraform and copy the `dotenv_snippet` output into your .env."
    )
