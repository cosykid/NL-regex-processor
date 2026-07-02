"""Storage backend behaviour.

The local round-trip runs for real; the S3 cases assert only the pure
locator/URI/config mapping (string ops), so the suite stays hermetic — no
network, no boto3 client, no AWS.
"""
import io

import pytest

from jobs.models import UploadedFile
from processing import storage
from processing.file_inspect import inspect, read_window


# --------------------------------------------------------------------------- #
# Local backend (real filesystem round-trip)
# --------------------------------------------------------------------------- #
@pytest.fixture
def local(settings, tmp_path):
    settings.STORAGE_BACKEND = "local"
    settings.UPLOAD_DIR = tmp_path / "uploads"
    settings.RESULTS_DIR = tmp_path / "results"
    settings.UPLOAD_DIR.mkdir()
    settings.RESULTS_DIR.mkdir()
    return settings


def test_local_backend_selected(local):
    assert storage.backend_name() == "local"
    assert storage.spark_hadoop_conf() == {}  # no S3A config for local


def test_local_persist_moves_and_reads_back(local, tmp_path):
    src = tmp_path / "staging.csv"
    src.write_bytes(b"a,b\n1,2\n")

    locator = storage.upload_locator("abc-123", ".csv")
    size = storage.persist_upload(src, locator)

    assert size == 8
    assert not src.exists()  # a move, not a copy
    with storage.open_binary(locator) as fh:
        assert fh.read() == b"a,b\n1,2\n"


def test_local_result_locator_and_glob(local):
    locator = storage.result_locator("job-1")
    assert locator.endswith("job-1")
    assert storage.parquet_glob(locator).endswith("*.parquet")


def test_local_open_missing_raises_filenotfound(local):
    with pytest.raises(FileNotFoundError):
        storage.open_binary(storage.upload_locator("missing", ".csv"))


def test_local_delete_is_idempotent(local, tmp_path):
    src = tmp_path / "s.csv"
    src.write_bytes(b"x")
    locator = storage.upload_locator("d", ".csv")
    storage.persist_upload(src, locator)
    storage.delete(locator)
    storage.delete(locator)  # already gone -> no error
    with pytest.raises(FileNotFoundError):
        storage.open_binary(locator)


# --------------------------------------------------------------------------- #
# S3 backend (pure mapping — no network)
# --------------------------------------------------------------------------- #
@pytest.fixture
def s3(settings):
    settings.STORAGE_BACKEND = "s3"
    settings.S3_BUCKET = "my-bucket"
    settings.S3_REGION = "ap-southeast-2"
    settings.S3_ENDPOINT_URL = ""
    return settings


def test_s3_backend_selected(s3):
    assert storage.backend_name() == "s3"


def test_s3_locators_are_keys(s3):
    assert storage.upload_locator("abc", ".csv") == "uploads/abc.csv"
    assert storage.result_locator("job-9") == "results/job-9"


def test_s3_spark_uris(s3):
    assert storage.spark_read_uri("uploads/x.csv") == "s3a://my-bucket/uploads/x.csv"
    assert storage.spark_write_uri("results/j") == "s3a://my-bucket/results/j"


def test_s3_parquet_glob(s3):
    assert storage.parquet_glob("results/j") == "s3://my-bucket/results/j/*.parquet"


def test_s3_hadoop_conf_uses_default_chain(s3):
    conf = storage.spark_hadoop_conf()
    assert conf["spark.hadoop.fs.s3a.impl"] == "org.apache.hadoop.fs.s3a.S3AFileSystem"
    assert "DefaultAWSCredentialsProviderChain" in (
        conf["spark.hadoop.fs.s3a.aws.credentials.provider"]
    )
    assert conf["spark.hadoop.fs.s3a.endpoint"] == "s3.ap-southeast-2.amazonaws.com"


def test_s3_hadoop_conf_custom_endpoint(s3):
    s3.S3_ENDPOINT_URL = "http://minio:9000"
    conf = storage.spark_hadoop_conf()
    assert conf["spark.hadoop.fs.s3a.endpoint"] == "http://minio:9000"
    assert conf["spark.hadoop.fs.s3a.path.style.access"] == "true"


# --------------------------------------------------------------------------- #
# file_inspect works on an open binary stream (the shape the S3 backend hands it)
# --------------------------------------------------------------------------- #
def test_inspect_accepts_a_stream():
    stream = io.BytesIO(b"ID,Name\n1,John\n2,Jane\n")
    info = inspect(stream, UploadedFile.Kind.CSV)
    assert info["columns"] == ["ID", "Name"]
    assert info["preview_rows"][0]["Name"] == "John"


def test_read_window_accepts_a_stream():
    stream = io.BytesIO(b"ID,Val\n1,a\n2,b\n3,c\n")
    first = read_window(stream, UploadedFile.Kind.CSV, ["ID", "Val"], None, 2)
    assert [r["ID"] for r in first["rows"]] == ["1", "2"]
    assert first["eof"] is False

    nxt = read_window(
        stream, UploadedFile.Kind.CSV, ["ID", "Val"], first["cursor"], 2
    )
    assert [r["ID"] for r in nxt["rows"]] == ["3"]
    assert nxt["eof"] is True
