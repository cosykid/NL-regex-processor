"""HTTP API views, split by resource.

    uploads.py -- UploadView, UploadDetailView, UploadRowsView
    jobs.py    -- JobListCreateView, JobDetailView, JobCancelView,
                  JobResultsView, JobExportView

Everything is re-exported here so ``api/urls.py`` (``from . import views;
views.UploadView``) and the test suite (``from api import views;
views.process_job`` / ``views.cache`` / ``views.celery_app``) keep working
unchanged.
"""
from __future__ import annotations

from config.celery import app as celery_app
from processing import cache
from processing.tasks import process_job

from .jobs import (
    JobCancelView,
    JobDetailView,
    JobExportView,
    JobListCreateView,
    JobResultsView,
)
from .uploads import (
    UploadCompleteView,
    UploadDetailView,
    UploadMultipartAbortView,
    UploadMultipartCompleteView,
    UploadMultipartCreateView,
    UploadPresignView,
    UploadRowsView,
    UploadView,
)

__all__ = [
    "UploadView",
    "UploadPresignView",
    "UploadCompleteView",
    "UploadMultipartCreateView",
    "UploadMultipartCompleteView",
    "UploadMultipartAbortView",
    "UploadDetailView",
    "UploadRowsView",
    "JobListCreateView",
    "JobDetailView",
    "JobCancelView",
    "JobResultsView",
    "JobExportView",
    "celery_app",
    "cache",
    "process_job",
]
