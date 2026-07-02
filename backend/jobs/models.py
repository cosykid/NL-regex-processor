"""Data layer.

Two persisted entities:

* :class:`UploadedFile` — a file the user uploaded, plus the lightweight header
  inspection (column names + a small preview) used to drive the UI.
* :class:`Job` — one natural-language replacement request against an uploaded
  file. Carries the lifecycle status, progress, the resolved regex, and the
  location of the Spark-written result.
"""
from __future__ import annotations

import uuid

from django.db import models


class UploadedFile(models.Model):
    class Kind(models.TextChoices):
        CSV = "csv", "CSV"
        EXCEL = "excel", "Excel"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    original_name = models.CharField(max_length=512)
    kind = models.CharField(max_length=16, choices=Kind.choices)
    path = models.CharField(max_length=1024)
    size_bytes = models.BigIntegerField(default=0)
    # Column names from a cheap header inspection at upload time. The row preview
    # itself is NOT stored — it's re-read from object storage on demand (the file
    # is the source of truth), so raw sample rows don't live in the metadata DB.
    columns = models.JSONField(default=list)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self) -> str:  # pragma: no cover - admin convenience
        return f"{self.original_name} ({self.id})"


class Job(models.Model):
    class Status(models.TextChoices):
        QUEUED = "QUEUED", "Queued"
        RUNNING = "RUNNING", "Running"
        SUCCESS = "SUCCESS", "Success"
        FAILED = "FAILED", "Failed"
        CANCELLED = "CANCELLED", "Cancelled"

    class RegexSource(models.TextChoices):
        CACHE = "cache", "Redis cache"
        LLM = "llm", "LLM"
        HEURISTIC = "heuristic", "Heuristic fallback"

    TERMINAL_STATUSES = {Status.SUCCESS, Status.FAILED, Status.CANCELLED}

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    uploaded_file = models.ForeignKey(
        UploadedFile, on_delete=models.CASCADE, related_name="jobs"
    )

    # --- request ---
    nl_prompt = models.TextField()
    replacement_value = models.TextField(blank=True, default="")
    target_columns = models.JSONField(default=list)

    # --- lifecycle ---
    status = models.CharField(
        max_length=16, choices=Status.choices, default=Status.QUEUED
    )
    progress = models.PositiveSmallIntegerField(default=0)  # 0..100
    stage = models.CharField(max_length=64, blank=True, default="queued")
    celery_task_id = models.CharField(max_length=255, blank=True, default="")
    error_message = models.TextField(blank=True, default="")

    # --- resolved regex ---
    regex_pattern = models.TextField(blank=True, default="")
    regex_source = models.CharField(
        max_length=16, choices=RegexSource.choices, blank=True, default=""
    )
    regex_explanation = models.TextField(blank=True, default="")

    # --- result ---
    result_path = models.CharField(max_length=1024, blank=True, default="")
    total_rows = models.BigIntegerField(null=True, blank=True)
    matched_rows = models.BigIntegerField(null=True, blank=True)
    result_columns = models.JSONField(default=list)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self) -> str:  # pragma: no cover - admin convenience
        return f"Job {self.id} [{self.status}]"

    @property
    def is_terminal(self) -> bool:
        return self.status in self.TERMINAL_STATUSES
