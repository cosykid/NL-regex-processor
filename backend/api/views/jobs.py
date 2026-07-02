"""Job endpoints.

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

from django.http import StreamingHttpResponse
from django.shortcuts import get_object_or_404
from rest_framework import status
from rest_framework.generics import ListAPIView
from rest_framework.response import Response
from rest_framework.views import APIView

from config.celery import app as celery_app
from jobs.models import Job
from processing import cache, results
from processing.tasks import process_job

from .. import exports
from ..params import _truthy
from ..serializers import JobCreateSerializer, JobSerializer


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
        spec = exports.get_format(fmt)
        if spec is None:
            return Response(
                {"detail": f"Unsupported export format '{fmt}'. Use csv or xlsx."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        matched_only = _truthy(request.query_params.get("matched_only"))
        try:
            tmp_path = spec.build(job.result_path, matched_only=matched_only)
        except results.ResultTooLargeForExcel as exc:
            # Asking for more rows than a sheet can hold is a bad request, not a
            # server fault — surface the message (it points the user to CSV).
            return Response({"detail": str(exc)}, status=status.HTTP_400_BAD_REQUEST)
        except Exception as exc:  # noqa: BLE001 - surface a clean error
            return Response(
                {"detail": f"Could not build the export: {exc}"},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )

        filename = exports.export_filename(
            job.uploaded_file.original_name, spec.ext, matched_only
        )

        response = StreamingHttpResponse(
            exports.stream_file(tmp_path), content_type=spec.content_type
        )
        response["Content-Disposition"] = f'attachment; filename="{filename}"'
        return response
