"""Export-format registry and streaming helpers for ``JobExportView``.

Keeps the "how do we turn a result into a downloadable file" concerns (format
registry, chunked file streaming, filename construction) out of the view so it
can stay a thin HTTP-mapping layer.
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from django.utils.text import slugify

from processing import results


@dataclass(frozen=True)
class ExportFormat:
    """One exportable file format: how to build it and how to label it."""

    build: Callable[..., Path | str]
    ext: str
    content_type: str


EXPORT_FORMATS: dict[str, ExportFormat] = {
    "csv": ExportFormat(
        build=results.write_csv,
        ext="csv",
        content_type="text/csv; charset=utf-8",
    ),
    "xlsx": ExportFormat(
        build=results.write_xlsx,
        ext="xlsx",
        content_type=(
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        ),
    ),
}


def get_format(fmt: str) -> ExportFormat | None:
    return EXPORT_FORMATS.get(fmt)


def stream_file(path, chunk: int = 64 * 1024):
    """Yield ``path`` in chunks, deleting the (temporary) file once exhausted."""
    try:
        with open(path, "rb") as fh:
            while True:
                data = fh.read(chunk)
                if not data:
                    break
                yield data
    finally:
        try:
            os.unlink(path)
        except OSError:
            pass


def export_filename(original_name: str, ext: str, matched_only: bool) -> str:
    stem = slugify(Path(original_name).stem) or "export"
    suffix = "-affected" if matched_only else ""
    return f"{stem}{suffix}.{ext}"
