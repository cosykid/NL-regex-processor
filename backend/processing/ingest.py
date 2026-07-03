"""Upload ingestion service: stage -> inspect -> persist -> record.

Extracted from ``UploadView.post`` so the view stays a thin HTTP-mapping
layer. This module owns the stage-to-disk / inspect / persist-to-backend /
create-row flow; the view maps the typed result to HTTP responses.
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass
from pathlib import Path

from django.conf import settings

from jobs.models import UploadedFile
from . import storage
from .file_inspect import detect_kind, inspect


class UploadRejected(Exception):
    """The uploaded file could not be accepted; ``detail`` is user-facing."""

    def __init__(self, detail: str):
        super().__init__(detail)
        self.detail = detail


def plan_upload(filename: str):
    """Allocate an id, kind, and storage locator for a new upload.

    Returns ``(file_id, kind, locator)``. The presigned-upload flow needs the
    locator up front (before any bytes exist) to sign a PUT URL; ``save_upload``
    reuses it so both paths derive the id/kind/suffix identically.
    """
    kind = detect_kind(filename)
    file_id = uuid.uuid4()
    suffix = Path(filename).suffix or (
        ".xlsx" if kind == UploadedFile.Kind.EXCEL else ".csv"
    )
    return file_id, kind, storage.upload_locator(file_id, suffix)


@dataclass
class SavedUpload:
    uploaded: UploadedFile
    preview_rows: list[dict]


def save_upload(django_file) -> SavedUpload:
    """Stage, inspect, persist, and record a freshly-uploaded file.

    ``django_file`` is a Django ``UploadedFile`` (``request.FILES["file"]``).
    Raises :class:`UploadRejected` for any user-facing validation failure
    (unparsable file, no header/columns) so the view can turn it into a 400.
    """
    file_id, kind, locator = plan_upload(django_file.name)
    suffix = Path(locator).suffix

    # Big uploads Django already streamed to a temp file expose a real path
    # (``temporary_file_path``); inspect + persist straight from it so we never
    # re-copy the whole file. Small in-memory uploads have no path, so we spill
    # them to a staging file first. Either way we inspect only the header
    # locally, then hand the file to the storage backend: a rename for `local`,
    # a multipart upload for `s3`.
    temp_path = getattr(django_file, "temporary_file_path", None)
    if temp_path is not None:
        staging, owns_staging = Path(temp_path()), False
    else:
        staging, owns_staging = _stage_to_disk(django_file, suffix), True

    try:
        try:
            with open(staging, "rb") as fh:
                info = inspect(fh, kind)
        except Exception as exc:  # noqa: BLE001 - surface a clean parse error
            raise UploadRejected(f"Could not parse the file: {exc}") from exc

        if not info["columns"]:
            raise UploadRejected(
                "The file appears to have no header row / columns."
            )

        # Moves the file out of `staging` on the local backend; on `s3` it
        # uploads and leaves the source for its owner (us / Django) to clean up.
        size_bytes = storage.persist_upload(staging, locator)
    finally:
        # Only clean up a staging file we created. Django owns (and deletes) its
        # own temp file; if persist_upload already moved ours away, this no-ops.
        if owns_staging:
            staging.unlink(missing_ok=True)

    _enforce_size_limit(size_bytes, locator)

    return _record(
        file_id, django_file.name, kind, locator, size_bytes, info
    )


def register_stored_upload(
    file_id, name: str, kind: str, locator: str
) -> SavedUpload:
    """Record an upload whose bytes already live at ``locator``.

    Used when the client uploaded straight to object storage via a presigned
    URL (see ``UploadCompleteView``): the web process never handles the bytes,
    it only reads the header (a ranged GET on S3) to get columns + preview.
    Raises :class:`UploadRejected` if the object is missing or unparsable.
    """
    try:
        size_bytes = storage.size_bytes(locator)
    except FileNotFoundError as exc:
        raise UploadRejected("The uploaded file was not found in storage.") from exc

    # The browser uploaded straight to storage, so enforce the size cap after
    # the fact and evict an over-limit object.
    _enforce_size_limit(size_bytes, locator)

    try:
        with storage.open_binary(locator) as fh:
            info = inspect(fh, kind)
    except FileNotFoundError as exc:
        raise UploadRejected("The uploaded file was not found in storage.") from exc
    except Exception as exc:  # noqa: BLE001 - surface a clean parse error
        raise UploadRejected(f"Could not parse the file: {exc}") from exc

    if not info["columns"]:
        raise UploadRejected("The file appears to have no header row / columns.")

    return _record(file_id, name, kind, locator, size_bytes, info)


def _enforce_size_limit(size_bytes: int, locator: str) -> None:
    """Reject (and evict) an upload that exceeds ``MAX_UPLOAD_BYTES``."""
    if size_bytes > settings.MAX_UPLOAD_BYTES:
        storage.delete(locator)
        limit_mb = settings.MAX_UPLOAD_BYTES // (1024 * 1024)
        raise UploadRejected(f"File exceeds the {limit_mb} MB upload limit.")


def _stage_to_disk(django_file, suffix: str) -> Path:
    """Stream an in-memory upload to a local staging file in chunks.

    Only used for uploads small enough that Django kept them in memory
    (``FILE_UPLOAD_MAX_MEMORY_SIZE``); larger ones already live in a temp file
    we can use directly. Never buffers the whole file in the web process.
    """
    staging = storage.staging_path(suffix)
    with open(staging, "wb") as out:
        for chunk in django_file.chunks():
            out.write(chunk)
    return staging


def _record(file_id, name, kind, locator, size_bytes, info) -> SavedUpload:
    uploaded = UploadedFile.objects.create(
        id=file_id,
        original_name=name,
        kind=kind,
        path=locator,
        size_bytes=size_bytes,
        columns=info["columns"],
    )
    return SavedUpload(uploaded=uploaded, preview_rows=info["preview_rows"])
