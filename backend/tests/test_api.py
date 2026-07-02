import types

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from rest_framework.test import APIClient

from jobs.models import Job, UploadedFile

pytestmark = pytest.mark.django_db


@pytest.fixture
def client():
    return APIClient()


@pytest.fixture(autouse=True)
def isolate_storage(settings, tmp_path):
    # Pin the local backend so the suite never touches a real S3 bucket, even
    # when the ambient env sets STORAGE_BACKEND=s3.
    settings.STORAGE_BACKEND = "local"
    up = tmp_path / "uploads"
    res = tmp_path / "results"
    up.mkdir()
    res.mkdir()
    settings.UPLOAD_DIR = up
    settings.RESULTS_DIR = res


@pytest.fixture(autouse=True)
def stub_dispatch(monkeypatch):
    # Don't actually enqueue Celery work in API tests.
    from api import views

    monkeypatch.setattr(
        views.process_job,
        "delay",
        lambda *a, **k: types.SimpleNamespace(id="fake-task-id"),
    )


def _upload_csv(client):
    csv = b"ID,Name,Email\n1,John,john@example.com\n2,Jane,jane@domain.com\n"
    f = SimpleUploadedFile("people.csv", csv, content_type="text/csv")
    return client.post("/api/uploads", {"file": f}, format="multipart")


def test_upload_returns_columns(client):
    resp = _upload_csv(client)
    assert resp.status_code == 201
    body = resp.json()
    assert body["columns"] == ["ID", "Name", "Email"]
    assert UploadedFile.objects.count() == 1


def test_upload_rows_windows_the_original(client):
    rows = b"".join(f"{i},n{i}\n".encode() for i in range(1, 6))
    f = SimpleUploadedFile("nums.csv", b"ID,Name\n" + rows, content_type="text/csv")
    upload_id = client.post(
        "/api/uploads", {"file": f}, format="multipart"
    ).json()["id"]

    first = client.get(f"/api/uploads/{upload_id}/rows?limit=2")
    assert first.status_code == 200
    body = first.json()
    assert [r["ID"] for r in body["rows"]] == ["1", "2"]
    assert body["eof"] is False
    cursor = body["cursor"]
    assert cursor

    # Resume from the cursor rather than re-scanning from the top.
    mid = client.get(f"/api/uploads/{upload_id}/rows?cursor={cursor}&limit=2").json()
    assert [r["ID"] for r in mid["rows"]] == ["3", "4"]

    last = client.get(
        f"/api/uploads/{upload_id}/rows?cursor={mid['cursor']}&limit=2"
    ).json()
    assert [r["ID"] for r in last["rows"]] == ["5"]
    assert last["eof"] is True
    assert last["cursor"] is None


def test_upload_rows_rejects_bad_cursor(client):
    upload_id = _upload_csv(client).json()["id"]
    resp = client.get(f"/api/uploads/{upload_id}/rows?cursor=notanumber")
    assert resp.status_code == 400


def test_create_job_dispatches_and_returns_queued(client):
    upload_id = _upload_csv(client).json()["id"]
    resp = client.post(
        "/api/jobs",
        {
            "uploaded_file": upload_id,
            "nl_prompt": "find email addresses",
            "replacement_value": "REDACTED",
            "target_columns": ["Email"],
        },
        format="json",
    )
    assert resp.status_code == 201
    body = resp.json()
    assert body["status"] == "QUEUED"
    assert body["target_columns"] == ["Email"]
    job = Job.objects.get(id=body["id"])
    assert job.celery_task_id == "fake-task-id"


def test_create_job_rejects_unknown_column(client):
    upload_id = _upload_csv(client).json()["id"]
    resp = client.post(
        "/api/jobs",
        {
            "uploaded_file": upload_id,
            "nl_prompt": "find emails",
            "target_columns": ["NopeColumn"],
        },
        format="json",
    )
    assert resp.status_code == 400
    assert "target_columns" in resp.json()


def test_results_conflict_when_not_ready(client):
    upload_id = _upload_csv(client).json()["id"]
    job_id = client.post(
        "/api/jobs",
        {
            "uploaded_file": upload_id,
            "nl_prompt": "find emails",
            "target_columns": ["Email"],
        },
        format="json",
    ).json()["id"]
    resp = client.get(f"/api/jobs/{job_id}/results")
    assert resp.status_code == 409


def test_cancel_marks_cancelled(client, monkeypatch):
    # Avoid hitting the Celery control bus / Redis in the cancel path.
    from api import views

    monkeypatch.setattr(views.cache, "request_cancel", lambda *a, **k: None)
    monkeypatch.setattr(
        views.celery_app.control, "revoke", lambda *a, **k: None
    )

    upload_id = _upload_csv(client).json()["id"]
    job_id = client.post(
        "/api/jobs",
        {
            "uploaded_file": upload_id,
            "nl_prompt": "find emails",
            "target_columns": ["Email"],
        },
        format="json",
    ).json()["id"]

    resp = client.post(f"/api/jobs/{job_id}/cancel")
    assert resp.status_code == 200
    assert resp.json()["status"] == "CANCELLED"
