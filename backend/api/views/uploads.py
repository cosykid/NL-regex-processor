"""Upload endpoints.

    POST   /uploads              multipart upload -> {id, columns, preview}
    GET    /uploads/<id>         upload metadata
    GET    /uploads/<id>/rows    windowed view of the raw upload (?cursor=&limit=)
                                 for scrolling the original before any transform
"""
from __future__ import annotations

import math

from django.shortcuts import get_object_or_404
from rest_framework import status
from rest_framework.parsers import FormParser, MultiPartParser
from rest_framework.response import Response
from rest_framework.views import APIView

from jobs.models import UploadedFile
from processing import cache, storage
from processing.file_inspect import read_window
from processing.ingest import (
    UploadRejected,
    plan_upload,
    register_stored_upload,
    save_upload,
)

from ..serializers import UploadedFileSerializer

# How long a presigned PUT URL (and its pending-upload record) stays valid.
PRESIGN_TTL_SECONDS = 3600

# S3 requires every part except the last to be >= 5 MB, and at most 10000 parts
# per upload. We size parts so the count stays modest (aim for <= ~64 parts on
# typical files) while never dropping below the 5 MB floor.
MULTIPART_MIN_PART_SIZE = 5 * 1024 * 1024
MULTIPART_MAX_PARTS = 10000
MULTIPART_TARGET_PARTS = 64


def _created_response(saved) -> Response:
    """The 201 payload every upload-creation path returns.

    The serialized row plus the freshly-inspected preview. `preview_rows` is
    computed inline and never persisted — the client seeds its original-file
    view from it, then pages the rest via /uploads/<id>/rows from storage.
    """
    data = UploadedFileSerializer(saved.uploaded).data
    data["preview_rows"] = saved.preview_rows
    return Response(data, status=status.HTTP_201_CREATED)


def _plan_parts(size: int) -> tuple[int, int]:
    """Choose ``(part_size, num_parts)`` covering a ``size``-byte object.

    Parts are at least 5 MB; a very large file is packed into <= 10000 parts by
    growing the part size instead of the count.
    """
    part_size = max(MULTIPART_MIN_PART_SIZE, math.ceil(size / MULTIPART_TARGET_PARTS))
    num_parts = math.ceil(size / part_size)
    if num_parts > MULTIPART_MAX_PARTS:
        part_size = math.ceil(size / MULTIPART_MAX_PARTS)
        num_parts = math.ceil(size / part_size)
    return part_size, num_parts


class UploadView(APIView):
    parser_classes = [MultiPartParser, FormParser]

    def post(self, request):
        upload = request.FILES.get("file")
        if upload is None:
            return Response(
                {"detail": "No file provided (form field 'file')."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        try:
            saved = save_upload(upload)
        except UploadRejected as exc:
            return Response({"detail": exc.detail}, status=status.HTTP_400_BAD_REQUEST)

        return _created_response(saved)


class UploadPresignView(APIView):
    """Start a direct-to-storage upload.

    ``POST {"filename": "..."}`` →
      * S3 backend:  ``{"mode": "s3", "id", "url"}`` — the client PUTs the file
        straight to ``url`` (its bytes never touch the web process), then calls
        ``/uploads/complete`` with ``id``.
      * local backend: ``{"mode": "direct"}`` — no browser-reachable target, so
        the client falls back to a normal multipart ``POST /uploads``.
    """

    def post(self, request):
        if not storage.direct_upload_supported():
            return Response({"mode": "direct"})

        filename = (request.data.get("filename") or "").strip()
        if not filename:
            return Response(
                {"detail": "filename is required."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        file_id, kind, locator = plan_upload(filename)
        url = storage.presigned_put_url(locator, expires=PRESIGN_TTL_SECONDS)
        cache.set_pending_upload(
            file_id,
            {"name": filename, "kind": kind, "locator": locator},
            ttl=PRESIGN_TTL_SECONDS,
        )
        return Response({"mode": "s3", "id": str(file_id), "url": url})


class UploadCompleteView(APIView):
    """Finalize a direct-to-storage upload.

    ``POST {"id": "..."}`` — looks up the pending record stashed at presign
    time (so the client can't dictate the storage key or kind), reads only the
    header of the now-uploaded object, records it, and returns the same payload
    as ``POST /uploads``.
    """

    def post(self, request):
        upload_id = (request.data.get("id") or "").strip()
        pending = cache.get_pending_upload(upload_id) if upload_id else None
        if not pending:
            return Response(
                {"detail": "Upload session expired or unknown; please retry."},
                status=status.HTTP_410_GONE,
            )

        try:
            saved = register_stored_upload(
                upload_id, pending["name"], pending["kind"], pending["locator"]
            )
        except UploadRejected as exc:
            return Response({"detail": exc.detail}, status=status.HTTP_400_BAD_REQUEST)
        finally:
            cache.clear_pending_upload(upload_id)

        return _created_response(saved)


class UploadMultipartCreateView(APIView):
    """Begin a parallel multipart upload straight to S3.

    ``POST {"filename": "...", "size": <bytes>}`` → ``{"id", "part_size",
    "parts": [{"part_number", "url"}, ...]}``. The client slices the file into
    ``part_size`` chunks and PUTs each part to its presigned ``url`` in parallel
    (bounded concurrency) so total throughput isn't capped by one TCP stream,
    then calls ``/uploads/multipart/complete`` with the collected ETags.

    Only reached when the client already knows the backend is S3 (presign told
    it so); the server still decides the storage key, kind, and S3 upload id —
    the client only ever echoes back an opaque ``id``.
    """

    def post(self, request):
        filename = (request.data.get("filename") or "").strip()
        if not filename:
            return Response(
                {"detail": "filename is required."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        try:
            size = int(request.data.get("size"))
        except (TypeError, ValueError):
            return Response(
                {"detail": "size (bytes) is required."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        if size <= 0:
            return Response(
                {"detail": "size must be a positive integer."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        file_id, kind, locator = plan_upload(filename)
        upload_id = storage.create_multipart(locator)
        part_size, num_parts = _plan_parts(size)
        parts = [
            {
                "part_number": n,
                "url": storage.presigned_upload_part_url(
                    locator, upload_id, n, expires=PRESIGN_TTL_SECONDS
                ),
            }
            for n in range(1, num_parts + 1)
        ]
        cache.set_pending_upload(
            file_id,
            {
                "name": filename,
                "kind": kind,
                "locator": locator,
                "upload_id": upload_id,
            },
            ttl=PRESIGN_TTL_SECONDS,
        )
        return Response(
            {"id": str(file_id), "part_size": part_size, "parts": parts}
        )


class UploadMultipartCompleteView(APIView):
    """Finalize a multipart upload.

    ``POST {"id": "...", "parts": [{"part_number", "etag"}, ...]}`` — looks up
    the pending record stashed at create time (so the client can't dictate the
    storage key, kind, or S3 upload id), assembles the parts into the final
    object, reads only its header, and returns the same payload as
    ``POST /uploads``.
    """

    def post(self, request):
        record_id = (request.data.get("id") or "").strip()
        pending = cache.get_pending_upload(record_id) if record_id else None
        if not pending:
            return Response(
                {"detail": "Upload session expired or unknown; please retry."},
                status=status.HTTP_410_GONE,
            )

        try:
            parts = sorted(
                (
                    {"PartNumber": int(p["part_number"]), "ETag": p["etag"]}
                    for p in (request.data.get("parts") or [])
                ),
                key=lambda p: p["PartNumber"],
            )
        except (KeyError, TypeError, ValueError):
            return Response(
                {"detail": "Invalid parts payload."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        if not parts:
            return Response(
                {"detail": "No parts provided."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        # Assemble first, then register. A hard S3 failure here leaves the
        # pending record intact so the client can still abort by id; only clear
        # it once the object exists (whether it then validates or not).
        storage.complete_multipart(pending["locator"], pending["upload_id"], parts)
        try:
            saved = register_stored_upload(
                record_id, pending["name"], pending["kind"], pending["locator"]
            )
        except UploadRejected as exc:
            return Response({"detail": exc.detail}, status=status.HTTP_400_BAD_REQUEST)
        finally:
            cache.clear_pending_upload(record_id)

        return _created_response(saved)


class UploadMultipartAbortView(APIView):
    """Cancel an in-progress multipart upload.

    ``POST {"id": "..."}`` — the client calls this on cancel or unrecoverable
    part failure so already-uploaded parts don't linger. Idempotent: an unknown
    or already-cleared id is a no-op 204.
    """

    def post(self, request):
        record_id = (request.data.get("id") or "").strip()
        pending = cache.get_pending_upload(record_id) if record_id else None
        if not pending:
            return Response(status=status.HTTP_204_NO_CONTENT)
        try:
            storage.abort_multipart(pending["locator"], pending["upload_id"])
        finally:
            cache.clear_pending_upload(record_id)
        return Response(status=status.HTTP_204_NO_CONTENT)


class UploadDetailView(APIView):
    def get(self, request, pk):
        uploaded = get_object_or_404(UploadedFile, pk=pk)
        return Response(UploadedFileSerializer(uploaded).data)


class UploadRowsView(APIView):
    """A ``limit``-row window of the raw upload, for scrolling the original
    dataset before any transformation has been applied.

    Continuation is cursor-based: the response carries a ``cursor`` that the
    next request passes back (``?cursor=``), so a sequential scroll never
    re-reads rows it already fetched. The first request omits the cursor.
    """

    def get(self, request, pk):
        uploaded = get_object_or_404(UploadedFile, pk=pk)
        try:
            limit = int(request.query_params.get("limit", 100))
        except ValueError:
            return Response(
                {"detail": "limit must be an integer."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        limit = max(1, min(limit, 500))
        cursor = request.query_params.get("cursor") or None

        try:
            with storage.open_binary(uploaded.path) as fh:
                data = read_window(
                    fh, uploaded.kind, uploaded.columns, cursor, limit
                )
        except FileNotFoundError:
            return Response(
                {"detail": "The uploaded file is no longer available."},
                status=status.HTTP_410_GONE,
            )
        except (ValueError, OSError):
            return Response(
                {"detail": "Invalid cursor."}, status=status.HTTP_400_BAD_REQUEST
            )
        data["limit"] = limit
        return Response(data)
