from django.urls import path

from . import views

urlpatterns = [
    path("uploads", views.UploadView.as_view(), name="upload-create"),
    path("uploads/<uuid:pk>", views.UploadDetailView.as_view(), name="upload-detail"),
    path("uploads/<uuid:pk>/rows", views.UploadRowsView.as_view(), name="upload-rows"),
    path("jobs", views.JobListCreateView.as_view(), name="job-list-create"),
    path("jobs/<uuid:pk>", views.JobDetailView.as_view(), name="job-detail"),
    path("jobs/<uuid:pk>/cancel", views.JobCancelView.as_view(), name="job-cancel"),
    path("jobs/<uuid:pk>/results", views.JobResultsView.as_view(), name="job-results"),
    path("jobs/<uuid:pk>/export", views.JobExportView.as_view(), name="job-export"),
]
