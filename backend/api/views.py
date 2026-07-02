"""HTTP API.

Endpoints (all under ``/api/``):

    POST   /uploads              multipart upload -> {id, columns, preview}
    GET    /uploads/<id>         upload metadata
    GET    /uploads/<id>/rows    windowed view of the raw upload (?cursor=&limit=)
                                 for scrolling the original before any transform
    POST   /jobs                 create a job, dispatch async work, return job id
    GET    /jobs[?uploaded_file=] list jobs (optionally for one dataset)
    GET    /jobs/<id>            poll status / progress / resolved regex
    POST   /jobs/<id>/cancel     request cancellation of a running/queued job
    GET    /jobs/<id>/results    paged view of the processed result
                                 (?matched_only=true -> affected rows only)
    GET    /jobs/<id>/export     stream the processed result as a download
                                 (?fmt=csv|xlsx, default csv;
                                  ?matched_only=true -> affected rows only)

The create endpoint returns immediately with a job id — it never blocks on the
LLM or Spark; that work runs in the Celery worker.
"""
from __future__ import annotations

import os
import uuid
from pathlib import Path

from django.http import StreamingHttpResponse
from django.shortcuts import get_object_or_404
from django.utils.text import slugify
from rest_framework import status
from rest_framework.generics import ListAPIView
from rest_framework.parsers import FormParser, MultiPartParser
from rest_framework.response import Response
from rest_framework.views import APIView

from config.celery import app as celery_app
from jobs.models import Job, UploadedFile
from processing import cache, results, storage
from processing.file_inspect import detect_kind, inspect, read_window
from processing.tasks import process_job

from .serializers import (
    JobCreateSerializer,
    JobSerializer,
    UploadedFileSerializer,
)


class UploadView(APIView):
    parser_classes = [MultiPartParser, FormParser]

    def post(self, request):
        upload = request.FILES.get("file")
        if upload is None:
            return Response(
                {"detail": "No file provided (form field 'file')."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        kind = detect_kind(upload.name)
        file_id = uuid.uuid4()
        suffix = Path(upload.name).suffix or (
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
                for chunk in upload.chunks():
                    out.write(chunk)

            try:
                with open(staging, "rb") as fh:
                    info = inspect(fh, kind)
            except Exception as exc:  # noqa: BLE001 - surface a clean parse error
                return Response(
                    {"detail": f"Could not parse the file: {exc}"},
                    status=status.HTTP_400_BAD_REQUEST,
                )

            if not info["columns"]:
                return Response(
                    {"detail": "The file appears to have no header row / columns."},
                    status=status.HTTP_400_BAD_REQUEST,
                )

            size_bytes = storage.persist_upload(staging, locator)
        finally:
            staging.unlink(missing_ok=True)

        uploaded = UploadedFile.objects.create(
            id=file_id,
            original_name=upload.name,
            kind=kind,
            path=locator,
            size_bytes=size_bytes,
            columns=info["columns"],
        )
        # Return the freshly-inspected preview inline (computed above; no longer
        # persisted). The client seeds its original-file view from this, then
        # pages the rest via /uploads/<id>/rows straight from storage.
        data = UploadedFileSerializer(uploaded).data
        data["preview_rows"] = info["preview_rows"]
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


class JobListCreateView(ListAPIView):
    serializer_class = JobSerializer

    def get_queryset(self):
        # A dataset can be transformed any number of times; the UI lists a
        # single dataset's run history via ?uploaded_file=<id>.
        qs = Job.objects.all()
        uploaded_file = self.request.query_params.get("uploaded_file")
        if uploaded_file:
            qs = qs.filter(uploaded_file_id=uploaded_file)
        return qs

    def post(self, request):
        serializer = JobCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        job = serializer.save()

        # Dispatch the heavy work and return immediately with a job id.
        async_result = process_job.delay(str(job.id))
        Job.objects.filter(id=job.id).update(celery_task_id=async_result.id)
        job.refresh_from_db()

        return Response(JobSerializer(job).data, status=status.HTTP_201_CREATED)


class JobDetailView(APIView):
    def get(self, request, pk):
        job = get_object_or_404(Job, pk=pk)
        return Response(JobSerializer(job).data)


class JobCancelView(APIView):
    def post(self, request, pk):
        job = get_object_or_404(Job, pk=pk)
        if job.is_terminal:
            return Response(
                {"detail": f"Job already {job.status}.", "job": JobSerializer(job).data},
                status=status.HTTP_409_CONFLICT,
            )

        # 1) Flag for the running task / Spark poller to unwind gracefully.
        cache.request_cancel(job.id)
        # 2) Revoke so a not-yet-started task is skipped by the worker.
        if job.celery_task_id:
            celery_app.control.revoke(job.celery_task_id)
        # 3) Reflect the request in the DB right away (the task finalises it).
        Job.objects.filter(id=job.id).exclude(
            status__in=Job.TERMINAL_STATUSES
        ).update(status=Job.Status.CANCELLED, stage="cancelling")

        job.refresh_from_db()
        return Response(JobSerializer(job).data)


def _truthy(value: str | None) -> bool:
    return str(value).lower() in {"1", "true", "yes", "on"}


class JobResultsView(APIView):
    def get(self, request, pk):
        job = get_object_or_404(Job, pk=pk)
        if job.status != Job.Status.SUCCESS or not job.result_path:
            return Response(
                {"detail": f"Result not available (job is {job.status})."},
                status=status.HTTP_409_CONFLICT,
            )
        try:
            page = int(request.query_params.get("page", 1))
            page_size = int(request.query_params.get("page_size", 50))
        except ValueError:
            return Response(
                {"detail": "page and page_size must be integers."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        matched_only = _truthy(request.query_params.get("matched_only"))
        data = results.read_page(
            job.result_path, page, page_size, matched_only=matched_only
        )
        return Response(data)


_EXPORT_FORMATS = {
    "csv": {
        "build": results.write_csv,
        "ext": "csv",
        "content_type": "text/csv; charset=utf-8",
    },
    "xlsx": {
        "build": results.write_xlsx,
        "ext": "xlsx",
        "content_type": (
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        ),
    },
}


class JobExportView(APIView):
    """Stream the processed result as a downloadable CSV or Excel workbook."""

    def get(self, request, pk):
        job = get_object_or_404(Job, pk=pk)
        if job.status != Job.Status.SUCCESS or not job.result_path:
            return Response(
                {"detail": f"Result not available (job is {job.status})."},
                status=status.HTTP_409_CONFLICT,
            )

        # NB: the param is `fmt`, not `format` — DRF reserves `?format=` for
        # content negotiation (URL_FORMAT_OVERRIDE) and 404s on any value it has
        # no renderer for, so `?format=xlsx` would never reach this view.
        fmt = (request.query_params.get("fmt") or "csv").lower()
        spec = _EXPORT_FORMATS.get(fmt)
        if spec is None:
            return Response(
                {"detail": f"Unsupported export format '{fmt}'. Use csv or xlsx."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        matched_only = _truthy(request.query_params.get("matched_only"))
        try:
            tmp_path = spec["build"](job.result_path, matched_only=matched_only)
        except results.ResultTooLargeForExcel as exc:
            # Asking for more rows than a sheet can hold is a bad request, not a
            # server fault — surface the message (it points the user to CSV).
            return Response({"detail": str(exc)}, status=status.HTTP_400_BAD_REQUEST)
        except Exception as exc:  # noqa: BLE001 - surface a clean error
            return Response(
                {"detail": f"Could not build the export: {exc}"},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )

        def stream(path, chunk=64 * 1024):
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

        stem = slugify(Path(job.uploaded_file.original_name).stem) or "export"
        suffix = "-affected" if matched_only else ""
        filename = f"{stem}{suffix}.{spec['ext']}"

        response = StreamingHttpResponse(
            stream(tmp_path), content_type=spec["content_type"]
        )
        response["Content-Disposition"] = f'attachment; filename="{filename}"'
        return response
