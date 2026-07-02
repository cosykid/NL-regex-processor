"""Storage cleanup on row deletion (post_delete signals).

Runs entirely against the local backend with UPLOAD_DIR/RESULTS_DIR pinned at a
``tmp_path`` — no S3, no network. Deleting a row must unlink the object(s) it
points at, including through a cascade.
"""
from pathlib import Path

import pytest

from jobs.models import Job, UploadedFile
from processing import storage

pytestmark = pytest.mark.django_db


@pytest.fixture(autouse=True)
def isolate_storage(settings, tmp_path):
    # Pin the local backend at a temp dir so cleanup only ever touches tmp_path.
    settings.STORAGE_BACKEND = "local"
    up = tmp_path / "uploads"
    res = tmp_path / "results"
    up.mkdir()
    res.mkdir()
    settings.UPLOAD_DIR = up
    settings.RESULTS_DIR = res


def _make_upload() -> tuple[UploadedFile, str]:
    """An UploadedFile whose ``path`` points at a real local file."""
    locator = storage.upload_locator("cleanup-up", ".csv")
    Path(locator).write_bytes(b"ID,Name\n1,John\n")
    upload = UploadedFile.objects.create(
        original_name="people.csv",
        kind=UploadedFile.Kind.CSV,
        path=locator,
        size_bytes=15,
    )
    return upload, locator


def _write_result(job_id) -> str:
    """A fake Spark result directory with a Parquet part-file inside."""
    locator = storage.result_locator(job_id)
    Path(locator).mkdir(parents=True, exist_ok=True)
    (Path(locator) / "part-0.parquet").write_bytes(b"PAR1")
    return locator


def test_delete_upload_removes_stored_file():
    upload, locator = _make_upload()
    assert Path(locator).exists()

    upload.delete()

    assert not Path(locator).exists()


def test_delete_job_removes_result_objects():
    upload, _ = _make_upload()
    job = Job.objects.create(uploaded_file=upload, nl_prompt="find emails")
    job.result_path = _write_result(job.id)
    job.save(update_fields=["result_path"])
    assert Path(job.result_path).exists()

    result_path = job.result_path
    job.delete()

    assert not Path(result_path).exists()


def test_delete_job_without_result_is_a_noop():
    upload, _ = _make_upload()
    job = Job.objects.create(uploaded_file=upload, nl_prompt="find emails")
    assert job.result_path == ""

    job.delete()  # empty result_path -> nothing to unlink, must not raise


def test_deleting_upload_cascades_cleanup_to_job_result():
    # Deleting the upload cascade-deletes its Job; post_delete fires per row, so
    # both the upload file and the job's Parquet result get cleaned up.
    upload, upload_locator = _make_upload()
    job = Job.objects.create(uploaded_file=upload, nl_prompt="find emails")
    job.result_path = _write_result(job.id)
    job.save(update_fields=["result_path"])
    result_path = job.result_path

    upload.delete()

    assert not Path(upload_locator).exists()
    assert not Path(result_path).exists()
    assert not Job.objects.filter(id=job.id).exists()


def test_storage_failure_does_not_break_delete(monkeypatch):
    # A blowing-up storage.delete must be swallowed: the row still deletes.
    upload, _ = _make_upload()

    def boom(locator):
        raise RuntimeError("storage exploded")

    monkeypatch.setattr(storage, "delete", boom)

    upload.delete()  # must not raise despite the storage error

    assert not UploadedFile.objects.filter(id=upload.id).exists()
