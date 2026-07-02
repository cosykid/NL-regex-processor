"""Storage-object cleanup wired to row deletion.

When an :class:`~jobs.models.UploadedFile` or :class:`~jobs.models.Job` row is
deleted, the object(s) it points at in storage would otherwise orphan on
disk/in S3. These ``post_delete`` handlers unlink them.

``post_delete`` (rather than a ``delete()`` override) is deliberate: it also
fires for *cascade* deletes, so removing an ``UploadedFile`` — which
cascade-deletes its ``Job`` rows — triggers the per-``Job`` handler too, and
each job's Parquet result is cleaned up alongside the upload.

Cleanup is strictly best-effort: a storage failure is logged and swallowed so
it can never propagate out of the delete or abort a cascade mid-way.
"""
from __future__ import annotations

import logging

from django.db.models.signals import post_delete
from django.dispatch import receiver

from processing import storage

from .models import Job, UploadedFile

logger = logging.getLogger("processing")


@receiver(post_delete, sender=UploadedFile)
def _delete_upload_object(sender, instance: UploadedFile, **kwargs) -> None:
    """Remove the stored upload backing a deleted ``UploadedFile`` row."""
    _best_effort_delete(instance.path)


@receiver(post_delete, sender=Job)
def _delete_result_objects(sender, instance: Job, **kwargs) -> None:
    """Remove the Parquet result backing a deleted ``Job`` row (if any)."""
    if not instance.result_path:  # no Spark result was ever written
        return
    _best_effort_delete(instance.result_path)


def _best_effort_delete(locator: str) -> None:
    try:
        storage.delete(locator)
    except Exception:  # noqa: BLE001 - cleanup must never break the delete/cascade
        logger.warning("storage cleanup failed for %r", locator, exc_info=True)
