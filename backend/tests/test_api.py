import math
import types
from pathlib import Path

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


def test_presign_returns_direct_on_local_backend(client):
    # No browser-reachable target on the local backend, so the client is told
    # to fall back to a normal multipart POST.
    resp = client.post(
        "/api/uploads/presign", {"filename": "people.csv"}, format="json"
    )
    assert resp.status_code == 200
    assert resp.json() == {"mode": "direct"}


def test_direct_upload_presign_then_complete(client, monkeypatch):
    # Simulate the S3 flow: presign hands back an id + PUT url, the "browser"
    # writes the object (here, straight to the local locator), and complete
    # reads only the header to register it. Redis + S3 are stubbed out.
    from api.views import uploads as up_views
    from processing import storage

    pending: dict = {}
    monkeypatch.setattr(storage, "direct_upload_supported", lambda: True)
    monkeypatch.setattr(
        storage, "presigned_put_url", lambda locator, expires=3600: f"https://s3.test/{locator}"
    )
    monkeypatch.setattr(
        up_views.cache, "set_pending_upload",
        lambda uid, data, ttl: pending.__setitem__(str(uid), data),
    )
    monkeypatch.setattr(
        up_views.cache, "get_pending_upload", lambda uid: pending.get(str(uid))
    )
    monkeypatch.setattr(
        up_views.cache, "clear_pending_upload", lambda uid: pending.pop(str(uid), None)
    )

    pre = client.post(
        "/api/uploads/presign", {"filename": "people.csv"}, format="json"
    ).json()
    assert pre["mode"] == "s3"
    assert pre["url"].startswith("https://s3.test/")

    # The browser's PUT lands the bytes at the locator; write them ourselves.
    Path(pending[pre["id"]]["locator"]).write_bytes(
        b"ID,Name,Email\n1,John,john@example.com\n"
    )

    resp = client.post("/api/uploads/complete", {"id": pre["id"]}, format="json")
    assert resp.status_code == 201
    body = resp.json()
    assert body["columns"] == ["ID", "Name", "Email"]
    assert body["preview_rows"][0]["Name"] == "John"
    assert UploadedFile.objects.count() == 1
    assert pre["id"] not in pending  # pending record cleared


def test_complete_rejects_unknown_id(client, monkeypatch):
    from api.views import uploads as up_views

    monkeypatch.setattr(up_views.cache, "get_pending_upload", lambda uid: None)
    monkeypatch.setattr(up_views.cache, "clear_pending_upload", lambda uid: None)
    resp = client.post("/api/uploads/complete", {"id": "nope"}, format="json")
    assert resp.status_code == 410


def test_multipart_create_returns_part_urls_and_stashes_pending(client, monkeypatch):
    # S3 flow: create opens a multipart upload and hands back a presigned URL
    # per part; the server-decided locator + S3 upload id are stashed under our
    # opaque id. Redis + S3 are stubbed out.
    from api.views import uploads as up_views
    from processing import storage

    pending: dict = {}
    monkeypatch.setattr(storage, "create_multipart", lambda locator: "s3-upload-1")
    monkeypatch.setattr(
        storage,
        "presigned_upload_part_url",
        lambda locator, upload_id, part_number, expires=3600:
            f"https://s3.test/{locator}?partNumber={part_number}&uploadId={upload_id}",
    )
    monkeypatch.setattr(
        up_views.cache, "set_pending_upload",
        lambda uid, data, ttl: pending.__setitem__(str(uid), data),
    )

    size = 40 * 1024 * 1024  # 40 MB -> several 5 MB parts
    resp = client.post(
        "/api/uploads/multipart/create",
        {"filename": "big.csv", "size": size},
        format="json",
    )
    assert resp.status_code == 200
    body = resp.json()

    # part_size respects the 5 MB floor and the parts tile the whole file.
    assert body["part_size"] >= 5 * 1024 * 1024
    assert len(body["parts"]) == math.ceil(size / body["part_size"])
    assert [p["part_number"] for p in body["parts"]] == list(
        range(1, len(body["parts"]) + 1)
    )
    assert body["parts"][0]["url"].startswith("https://s3.test/")

    rec = pending[body["id"]]
    assert rec["upload_id"] == "s3-upload-1"  # server-decided, not client-supplied
    assert rec["locator"].endswith(".csv")
    assert rec["name"] == "big.csv"


def test_multipart_complete_registers_file(client, monkeypatch):
    # Complete assembles the parts (stubbed no-op) then reads the header off the
    # object the "browser" wrote to the locator, and registers it.
    from api.views import uploads as up_views
    from processing import storage

    pending: dict = {}
    monkeypatch.setattr(storage, "create_multipart", lambda locator: "s3-upload-1")
    monkeypatch.setattr(
        storage,
        "presigned_upload_part_url",
        lambda locator, upload_id, part_number, expires=3600: f"https://s3.test/{part_number}",
    )
    monkeypatch.setattr(
        storage, "complete_multipart", lambda locator, upload_id, parts: None
    )
    monkeypatch.setattr(
        up_views.cache, "set_pending_upload",
        lambda uid, data, ttl: pending.__setitem__(str(uid), data),
    )
    monkeypatch.setattr(
        up_views.cache, "get_pending_upload", lambda uid: pending.get(str(uid))
    )
    monkeypatch.setattr(
        up_views.cache, "clear_pending_upload", lambda uid: pending.pop(str(uid), None)
    )

    created = client.post(
        "/api/uploads/multipart/create",
        {"filename": "people.csv", "size": 20 * 1024 * 1024},
        format="json",
    ).json()

    # The browser's part PUTs land the bytes at the locator; write them here.
    Path(pending[created["id"]]["locator"]).write_bytes(
        b"ID,Name,Email\n1,John,john@example.com\n"
    )

    resp = client.post(
        "/api/uploads/multipart/complete",
        {
            "id": created["id"],
            "parts": [
                {"part_number": p["part_number"], "etag": f'"etag-{p["part_number"]}"'}
                for p in created["parts"]
            ],
        },
        format="json",
    )
    assert resp.status_code == 201
    body = resp.json()
    assert body["columns"] == ["ID", "Name", "Email"]
    assert body["preview_rows"][0]["Name"] == "John"
    assert UploadedFile.objects.count() == 1
    assert created["id"] not in pending  # pending record cleared


def test_multipart_abort_clears_pending(client, monkeypatch):
    from api.views import uploads as up_views
    from processing import storage

    aborted: dict = {}
    pending = {
        "abc": {
            "name": "big.csv",
            "kind": UploadedFile.Kind.CSV,
            "locator": "uploads/abc.csv",
            "upload_id": "s3-upload-1",
        }
    }
    monkeypatch.setattr(
        storage, "abort_multipart",
        lambda locator, upload_id: aborted.__setitem__("call", (locator, upload_id)),
    )
    monkeypatch.setattr(
        up_views.cache, "get_pending_upload", lambda uid: pending.get(str(uid))
    )
    monkeypatch.setattr(
        up_views.cache, "clear_pending_upload", lambda uid: pending.pop(str(uid), None)
    )

    resp = client.post("/api/uploads/multipart/abort", {"id": "abc"}, format="json")
    assert resp.status_code == 204
    assert "abc" not in pending
    assert aborted["call"] == ("uploads/abc.csv", "s3-upload-1")


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


def test_create_job_exposes_predicate_fields(client):
    # The response carries the multi-column matching shape; predicates stay empty
    # until the worker resolves them, and the combinator defaults to AND.
    upload_id = _upload_csv(client).json()["id"]
    resp = client.post(
        "/api/jobs",
        {
            "uploaded_file": upload_id,
            "nl_prompt": "name starts with J and email contains example",
            "target_columns": ["Name", "Email"],
        },
        format="json",
    )
    assert resp.status_code == 201
    body = resp.json()
    assert body["predicates"] == []
    assert body["combinator"] == "all"
    assert body["target_columns"] == ["Name", "Email"]


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
