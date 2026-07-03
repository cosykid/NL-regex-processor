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

    class Combinator(models.TextChoices):
        ALL = "all", "All (AND)"
        ANY = "any", "Any (OR)"

    class Action(models.TextChoices):
        # What to do with the matched text / rows. `AUTO` defers to the model:
        # the LLM (or heuristic) reads the verb in the prompt and picks one of
        # the concrete actions below. An explicit choice overrides that.
        AUTO = "auto", "Auto (AI decides)"
        FIND = "find", "Find only"      # report matches; edit nothing, keep every row
        REPLACE = "replace", "Replace"  # swap matched text (blank value = remove)
        MASK = "mask", "Mask"           # redact matched text with a mask token
        EXTRACT = "extract", "Extract"  # keep only the match, drop the rest of the cell
        KEEP = "keep", "Keep rows"      # keep only matched rows, drop the rest
        DROP = "drop", "Drop rows"      # drop matched rows, keep the rest

    # Concrete actions that rewrite cells vs. filter rows — used by the engine
    # and the task to decide the branch and whether a value is meaningful.
    # `FIND` belongs to neither: it only counts/flags matches, the data passes
    # through untouched.
    CELL_ACTIONS = {Action.REPLACE, Action.MASK, Action.EXTRACT}
    ROW_ACTIONS = {Action.KEEP, Action.DROP}

    TERMINAL_STATUSES = {Status.SUCCESS, Status.FAILED, Status.CANCELLED}

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    uploaded_file = models.ForeignKey(
        UploadedFile, on_delete=models.CASCADE, related_name="jobs"
    )

    # --- request ---
    nl_prompt = models.TextField()
    replacement_value = models.TextField(blank=True, default="")
    target_columns = models.JSONField(default=list)
    # The action the user asked for. `AUTO` lets the model infer it from the
    # prompt; any other value is an explicit override the model must respect.
    action = models.CharField(
        max_length=8, choices=Action.choices, default=Action.AUTO
    )

    # --- lifecycle ---
    status = models.CharField(
        max_length=16, choices=Status.choices, default=Status.QUEUED
    )
    progress = models.PositiveSmallIntegerField(default=0)  # 0..100
    stage = models.CharField(max_length=64, blank=True, default="queued")
    celery_task_id = models.CharField(max_length=255, blank=True, default="")
    error_message = models.TextField(blank=True, default="")

    # --- resolved regex ---
    # A row is selected when its target columns satisfy a set of per-column
    # predicates combined with `combinator`. `predicates` is the source of truth
    # for matching/replacement: a list of {"column", "pattern", "explanation"}.
    # A single-condition request over one column is just a one-element list, so
    # this subsumes the earlier single-pattern model. `regex_pattern` is kept as
    # a human-readable summary of the predicate set (e.g. for the pattern strip
    # and logs), not a single applied pattern.
    predicates = models.JSONField(default=list)
    combinator = models.CharField(
        max_length=8, choices=Combinator.choices, default=Combinator.ALL
    )
    regex_pattern = models.TextField(blank=True, default="")
    regex_source = models.CharField(
        max_length=16, choices=RegexSource.choices, blank=True, default=""
    )
    regex_explanation = models.TextField(blank=True, default="")
    # The concrete action that actually ran. When `action` is `AUTO` this is the
    # action the model chose (replace/mask/...); when `action` is explicit the two
    # match. Empty until the job has resolved its conditions. Lets the UI show
    # what happened ("Auto → Mask") without re-deriving it.
    resolved_action = models.CharField(
        max_length=8, choices=Action.choices, blank=True, default=""
    )

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
