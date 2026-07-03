from django.urls import path

from . import views

urlpatterns = [
    path("uploads", views.UploadView.as_view(), name="upload-create"),
    path("uploads/presign", views.UploadPresignView.as_view(), name="upload-presign"),
    path("uploads/complete", views.UploadCompleteView.as_view(), name="upload-complete"),
    path(
        "uploads/multipart/create",
        views.UploadMultipartCreateView.as_view(),
        name="upload-multipart-create",
    ),
    path(
        "uploads/multipart/complete",
        views.UploadMultipartCompleteView.as_view(),
        name="upload-multipart-complete",
    ),
    path(
        "uploads/multipart/abort",
        views.UploadMultipartAbortView.as_view(),
        name="upload-multipart-abort",
    ),
    path("uploads/<uuid:pk>", views.UploadDetailView.as_view(), name="upload-detail"),
    path("uploads/<uuid:pk>/rows", views.UploadRowsView.as_view(), name="upload-rows"),
    path("jobs", views.JobListCreateView.as_view(), name="job-list-create"),
    path("jobs/<uuid:pk>", views.JobDetailView.as_view(), name="job-detail"),
    path("jobs/<uuid:pk>/cancel", views.JobCancelView.as_view(), name="job-cancel"),
    path("jobs/<uuid:pk>/results", views.JobResultsView.as_view(), name="job-results"),
    path("jobs/<uuid:pk>/export", views.JobExportView.as_view(), name="job-export"),
]
