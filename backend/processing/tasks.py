r"""Celery orchestration for a replacement job.

The whole heavy pipeline lives here so the web process never blocks:

    QUEUED -> (regex generation) -> (Spark replacement) -> SUCCESS
                                                        \-> FAILED / CANCELLED

* **Regex generation** is part of the background work (cache -> LLM -> validate),
  not the request path.
* **Transient** failures (LLM/network) retry with exponential backoff; once
  retries are exhausted the job is marked FAILED with the reason.
* **Permanent** failures (bad column, unsafe regex, Spark error) fail the job
  immediately — retrying wouldn't help.
* **Cancellation** is cooperative: the API sets a Redis flag, the task and the
  Spark poller observe it and unwind to CANCELLED.
* **Progress** is mirrored to both the Job row (read by the polling API) and the
  Celery task state.
"""
from __future__ import annotations

import logging

from celery import shared_task
from django.conf import settings
from django.utils import timezone

from jobs.models import Job

from . import cache, file_inspect, llm, spark_engine, storage
from .exceptions import JobCancelled, ProcessingError, TransientError

logger = logging.getLogger("processing")

MAX_RETRIES = 3


def _set(job_id, **fields) -> None:
    """Lightweight partial update that won't clobber concurrent writes."""
    fields["updated_at"] = timezone.now()
    Job.objects.filter(id=job_id).update(**fields)


def _resolve_action(job: Job, conditions: dict) -> tuple[str, str]:
    """Pick the concrete action + value that will actually run.

    * Explicit request (``replace``/``mask``/.../not ``auto``): honoured as-is,
      with the value the user typed.
    * ``auto``: use the action the model inferred (``replace`` if it named none),
      and prefer the user's typed value over any value the model pulled from the
      prompt — so a filled box is never overridden.

    Returns ``(action, replacement_value)``. For row actions (keep/drop) and
    ``extract`` the value is unused downstream; for ``mask`` an empty value means
    "use the default mask token".
    """
    if job.action != Job.Action.AUTO:
        return job.action, job.replacement_value

    action = conditions.get("action") or Job.Action.REPLACE.value
    if action not in Job.Action.values or action == Job.Action.AUTO.value:
        action = Job.Action.REPLACE.value
    value = job.replacement_value or conditions.get("value") or ""
    return action, value


@shared_task(bind=True, name="processing.process_job", acks_late=True,
             max_retries=MAX_RETRIES)
def process_job(self, job_id: str) -> dict:
    job = Job.objects.get(id=job_id)

    def cancelled() -> bool:
        return cache.is_cancelled(job_id)

    def progress_cb(pct: int, stage: str) -> None:
        _set(job_id, progress=pct, stage=stage)
        self.update_state(state="PROGRESS", meta={"progress": pct, "stage": stage})

    # Re-entrancy: record which task currently owns this job.
    _set(job_id, celery_task_id=self.request.id)

    try:
        if cancelled():
            raise JobCancelled("Cancelled before start")

        _set(job_id, status=Job.Status.RUNNING, progress=5,
             stage="generating regex", error_message="")

        # 1) Natural language -> validated per-column predicates (cache -> LLM
        #    -> heuristic).
        # Show the LLM a few real values from each target column so the pattern
        # matches the data's actual case/format. The preview is re-read from
        # object storage on demand (no longer stored in the DB); it's a
        # best-effort hint, so fall back to none rather than fail the job.
        try:
            with storage.open_binary(job.uploaded_file.path) as fh:
                preview = file_inspect.inspect(
                    fh, job.uploaded_file.kind
                )["preview_rows"]
        except OSError as exc:
            logger.warning("Job %s: preview read for samples failed: %s", job_id, exc)
            preview = []
        samples = file_inspect.sample_values(
            preview,
            job.target_columns,
            per_column=settings.LLM_SAMPLE_VALUES_PER_COLUMN,
            max_len=settings.LLM_SAMPLE_VALUE_MAXLEN,
        )
        conditions = llm.generate_conditions(
            job.nl_prompt, job.target_columns, samples=samples, action=job.action
        )

        # Resolve the output action. An explicit request wins; `auto` defers to
        # the action the model inferred (defaulting to replace). On `auto` the
        # value the user typed still takes precedence over any value the model
        # pulled from the prompt, so an explicit box is never overridden.
        action, replacement_value = _resolve_action(job, conditions)

        _set(
            job_id,
            predicates=conditions["predicates"],
            combinator=conditions["combinator"],
            regex_pattern=llm.summarize_conditions(
                conditions["predicates"], conditions["combinator"]
            ),
            regex_source=conditions["source"],
            regex_explanation=conditions.get("explanation", ""),
            resolved_action=action,
            progress=12,
            stage="regex ready",
        )

        if cancelled():
            raise JobCancelled("Cancelled after regex generation")

        # 2) Distributed row selection + action via Spark.
        stats = spark_engine.run_replacement(
            job_id=str(job.id),
            input_locator=job.uploaded_file.path,
            kind=job.uploaded_file.kind,
            predicates=conditions["predicates"],
            combinator=conditions["combinator"],
            action=action,
            replacement_value=replacement_value,
            result_locator=storage.result_locator(job.id),
            progress_cb=progress_cb,
            cancel_cb=cancelled,
        )

        # 3) Done.
        _set(
            job_id,
            status=Job.Status.SUCCESS,
            progress=100,
            stage="completed",
            total_rows=stats.total_rows,
            matched_rows=stats.matched_rows,
            result_columns=stats.columns,
            result_path=stats.result_path,
        )
        logger.info("Job %s succeeded (%s rows, %s matched)",
                    job_id, stats.total_rows, stats.matched_rows)
        return {"status": "SUCCESS", "job_id": str(job_id)}

    except JobCancelled:
        cache.clear_cancel(job_id)
        _set(job_id, status=Job.Status.CANCELLED, stage="cancelled")
        logger.info("Job %s cancelled", job_id)
        return {"status": "CANCELLED", "job_id": str(job_id)}

    except TransientError as exc:
        if self.request.retries >= MAX_RETRIES:
            _set(job_id, status=Job.Status.FAILED, stage="failed",
                 error_message=f"Transient failure after {MAX_RETRIES} "
                               f"retries: {exc}")
            return {"status": "FAILED", "job_id": str(job_id)}
        countdown = min(60, 2 ** self.request.retries)
        _set(job_id, stage=f"retrying in {countdown}s "
                           f"({self.request.retries + 1}/{MAX_RETRIES})")
        logger.warning("Job %s transient error, retrying: %s", job_id, exc)
        raise self.retry(exc=exc, countdown=countdown)

    except ProcessingError as exc:
        # Everything permanent lands here (RegexGenerationError, UnsafeRegexError,
        # SparkProcessingError, ...): transient and cancel cases were caught above.
        _set(job_id, status=Job.Status.FAILED, stage="failed",
             error_message=str(exc))
        logger.warning("Job %s failed: %s", job_id, exc)
        return {"status": "FAILED", "job_id": str(job_id)}

    except Exception as exc:  # noqa: BLE001 - last-resort guard
        logger.exception("Job %s crashed", job_id)
        _set(job_id, status=Job.Status.FAILED, stage="failed",
             error_message=f"Unexpected error: {exc}")
        return {"status": "FAILED", "job_id": str(job_id)}
