"""Upload endpoints.

    POST   /uploads              multipart upload -> {id, columns, preview}
    GET    /uploads/<id>         upload metadata
    GET    /uploads/<id>/rows    windowed view of the raw upload (?cursor=&limit=)
                                 for scrolling the original before any transform
"""
from __future__ import annotations

from django.shortcuts import get_object_or_404
from rest_framework import status
from rest_framework.parsers import FormParser, MultiPartParser
from rest_framework.response import Response
from rest_framework.views import APIView

from jobs.models import UploadedFile
from processing import storage
from processing.file_inspect import read_window
from processing.ingest import UploadRejected, save_upload

from ..serializers import UploadedFileSerializer


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

        # Return the freshly-inspected preview inline (computed above; no longer
        # persisted). The client seeds its original-file view from this, then
        # pages the rest via /uploads/<id>/rows straight from storage.
        data = UploadedFileSerializer(saved.uploaded).data
        data["preview_rows"] = saved.preview_rows
        return Response(data, status=status.HTTP_201_CREATED)


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
