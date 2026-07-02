"""Upload ingestion service: stage -> inspect -> persist -> record.

Extracted from ``UploadView.post`` so the view stays a thin HTTP-mapping
layer. This module owns the stage-to-disk / inspect / persist-to-backend /
create-row flow; the view maps the typed result to HTTP responses.
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass
from pathlib import Path

from jobs.models import UploadedFile
from . import storage
from .file_inspect import detect_kind, inspect


class UploadRejected(Exception):
    """The uploaded file could not be accepted; ``detail`` is user-facing."""

    def __init__(self, detail: str):
        super().__init__(detail)
        self.detail = detail


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
    kind = detect_kind(django_file.name)
    file_id = uuid.uuid4()
    suffix = Path(django_file.name).suffix or (
        ".xlsx" if kind == UploadedFile.Kind.EXCEL else ".csv"
    )
    locator = storage.upload_locator(file_id, suffix)

    # Stream to a local staging file in chunks — never buffer the whole file
    # in the web process (.chunks() streams both temp-file and in-memory
    # uploads). We inspect the header locally, then hand the file to the
    # storage backend: a rename for `local`, a multipart upload for `s3`.
    staging = storage.staging_path(suffix)
    try:
        with open(staging, "wb") as out:
            for chunk in django_file.chunks():
                out.write(chunk)

        try:
            with open(staging, "rb") as fh:
                info = inspect(fh, kind)
        except Exception as exc:  # noqa: BLE001 - surface a clean parse error
            raise UploadRejected(f"Could not parse the file: {exc}") from exc

        if not info["columns"]:
            raise UploadRejected(
                "The file appears to have no header row / columns."
            )

        size_bytes = storage.persist_upload(staging, locator)
    finally:
        staging.unlink(missing_ok=True)

    uploaded = UploadedFile.objects.create(
        id=file_id,
        original_name=django_file.name,
        kind=kind,
        path=locator,
        size_bytes=size_bytes,
        columns=info["columns"],
    )
    return SavedUpload(uploaded=uploaded, preview_rows=info["preview_rows"])
