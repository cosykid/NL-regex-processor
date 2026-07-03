"""Object storage for uploads and Spark results.

One small interface, two backends:

* ``local`` (default) — files under ``DATA_DIR`` on the shared volume. No
  external services; used by the test suite and by anyone running without AWS.
  A *locator* is an absolute filesystem path.
* ``s3`` — an S3 bucket. A *locator* is an object key (``uploads/<id>.csv`` or
  ``results/<job_id>``). Authentication uses the **default AWS credential
  provider chain** (``boto3``/S3A/DuckDB all resolve it the same way), so the
  identical code path works with static keys locally and an IAM role once
  deployed — nothing here ever handles a secret directly.

The rest of the app only ever deals in opaque *locators* (persisted in
``UploadedFile.path`` / ``Job.result_path``) plus the functions below; it never
branches on which backend is active. Choose the backend with ``STORAGE_BACKEND``.
"""
from __future__ import annotations

import io
import os
import shutil
import tempfile
import threading
from pathlib import Path

from django.conf import settings

# --------------------------------------------------------------------------- #
# Local filesystem backend
# --------------------------------------------------------------------------- #
class _LocalBackend:
    name = "local"

    def upload_locator(self, file_id, suffix: str) -> str:
        return str(Path(settings.UPLOAD_DIR) / f"{file_id}{suffix}")

    def result_locator(self, job_id) -> str:
        return str(Path(settings.RESULTS_DIR) / str(job_id))

    def persist_upload(self, src: Path, locator: str) -> int:
        dest = Path(locator)
        dest.parent.mkdir(parents=True, exist_ok=True)
        size = src.stat().st_size
        # Same volume as the staging file -> this is a cheap rename, not a copy.
        shutil.move(str(src), str(dest))
        return size

    def staging_path(self, suffix: str) -> Path:
        # Stage inside UPLOAD_DIR so persist_upload is a same-filesystem rename.
        d = Path(settings.UPLOAD_DIR)
        d.mkdir(parents=True, exist_ok=True)
        fd, name = tempfile.mkstemp(suffix=suffix, dir=str(d))
        os.close(fd)
        return Path(name)

    def open_binary(self, locator: str) -> io.BufferedReader:
        return open(locator, "rb")  # raises FileNotFoundError if missing

    def size_bytes(self, locator: str) -> int:
        return Path(locator).stat().st_size  # raises FileNotFoundError if missing

    def presigned_put_url(self, locator: str, expires: int) -> str:
        # No browser<->disk direct upload for the local backend; the client
        # falls back to a normal multipart POST (see UploadPresignView).
        raise NotImplementedError("local backend has no presigned upload")

    def create_multipart(self, locator: str) -> str:
        raise NotImplementedError("local backend has no multipart upload")

    def presigned_upload_part_url(
        self, locator: str, upload_id: str, part_number: int, expires: int
    ) -> str:
        raise NotImplementedError("local backend has no multipart upload")

    def complete_multipart(self, locator: str, upload_id: str, parts: list) -> None:
        raise NotImplementedError("local backend has no multipart upload")

    def abort_multipart(self, locator: str, upload_id: str) -> None:
        raise NotImplementedError("local backend has no multipart upload")

    def localize(self, locator: str, suffix: str = "") -> Path:
        # Already on the local filesystem — hand back the path as-is.
        return Path(locator)

    def delete(self, locator: str) -> None:
        # A locator is either a single uploaded file or a Spark result
        # *directory* of Parquet part-files; remove whichever it is. Idempotent:
        # a missing path is a no-op (mirrors the S3 backend's 404-tolerance).
        p = Path(locator)
        if p.is_dir():
            shutil.rmtree(p, ignore_errors=True)
        else:
            p.unlink(missing_ok=True)

    def spark_read_uri(self, locator: str) -> str:
        return str(locator)

    def spark_write_uri(self, locator: str) -> str:
        return str(locator)

    def parquet_glob(self, locator: str) -> str:
        return str(Path(locator) / "*.parquet")

    def spark_hadoop_conf(self) -> dict:
        return {}

    def configure_duckdb(self, con) -> None:
        return None


# --------------------------------------------------------------------------- #
# S3 backend
# --------------------------------------------------------------------------- #
_S3_CLIENT = None
_S3_CLIENT_LOCK = threading.Lock()


def _s3_client():
    """Lazily build (and cache) a boto3 S3 client from the default cred chain."""
    global _S3_CLIENT
    if _S3_CLIENT is None:
        with _S3_CLIENT_LOCK:
            if _S3_CLIENT is None:
                import boto3
                from botocore.config import Config

                # SigV4 so presigned URLs are valid in every region; path-style
                # addressing when a custom endpoint (MinIO/LocalStack) is set so
                # presigned PUTs resolve to host/bucket/key instead of a
                # virtual-host name the local endpoint can't serve.
                cfg = {"signature_version": "s3v4"}
                kwargs = {"region_name": settings.S3_REGION}
                if settings.S3_ENDPOINT_URL:  # MinIO / LocalStack
                    kwargs["endpoint_url"] = settings.S3_ENDPOINT_URL
                    cfg["s3"] = {"addressing_style": "path"}
                _S3_CLIENT = boto3.client("s3", config=Config(**cfg), **kwargs)
    return _S3_CLIENT


def _is_missing(exc) -> bool:
    from botocore.exceptions import ClientError

    if not isinstance(exc, ClientError):
        return False
    code = exc.response.get("Error", {}).get("Code")
    return code in {"404", "NoSuchKey", "NoSuchBucket", "NotFound"}


class _S3RawReader(io.RawIOBase):
    """Seekable, read-through view of an S3 object via ranged GETs.

    Wrapped in a ``BufferedReader`` by :meth:`_S3Backend.open_binary`, this makes
    the byte-offset CSV window logic and openpyxl's zip seeks work against S3
    unchanged — each buffered read maps to a bounded ``Range`` request, so a
    100-row window never pulls the whole object.
    """

    def __init__(self, client, bucket: str, key: str):
        self._client = client
        self._bucket = bucket
        self._key = key
        self._pos = 0
        self._size: int | None = None

    def _length(self) -> int:
        if self._size is None:
            try:
                head = self._client.head_object(Bucket=self._bucket, Key=self._key)
            except Exception as exc:  # noqa: BLE001
                if _is_missing(exc):
                    raise FileNotFoundError(f"s3://{self._bucket}/{self._key}") from exc
                raise
            self._size = int(head["ContentLength"])
        return self._size

    def readable(self) -> bool:
        return True

    def seekable(self) -> bool:
        return True

    def readinto(self, buf) -> int:
        want = len(buf)
        if want == 0:
            return 0
        length = self._length()
        if self._pos >= length:
            return 0
        end = min(self._pos + want, length) - 1
        try:
            resp = self._client.get_object(
                Bucket=self._bucket, Key=self._key, Range=f"bytes={self._pos}-{end}"
            )
            data = resp["Body"].read()
        except Exception as exc:  # noqa: BLE001
            if _is_missing(exc):
                raise FileNotFoundError(f"s3://{self._bucket}/{self._key}") from exc
            raise
        n = len(data)
        buf[:n] = data
        self._pos += n
        return n

    def seek(self, offset: int, whence: int = io.SEEK_SET) -> int:
        if whence == io.SEEK_SET:
            self._pos = offset
        elif whence == io.SEEK_CUR:
            self._pos += offset
        elif whence == io.SEEK_END:
            self._pos = self._length() + offset
        else:  # pragma: no cover - defensive
            raise ValueError(f"invalid whence: {whence}")
        return self._pos

    def tell(self) -> int:
        return self._pos


class _S3Backend:
    name = "s3"

    @property
    def _bucket(self) -> str:
        return settings.S3_BUCKET

    def upload_locator(self, file_id, suffix: str) -> str:
        return f"uploads/{file_id}{suffix}"

    def result_locator(self, job_id) -> str:
        return f"results/{job_id}"

    def persist_upload(self, src: Path, locator: str) -> int:
        size = src.stat().st_size
        _s3_client().upload_file(str(src), self._bucket, locator)
        return size

    def staging_path(self, suffix: str) -> Path:
        fd, name = tempfile.mkstemp(suffix=suffix)
        os.close(fd)
        return Path(name)

    def open_binary(self, locator: str) -> io.BufferedReader:
        reader = _S3RawReader(_s3_client(), self._bucket, locator)
        reader._length()  # surface a missing object now, as FileNotFoundError
        return io.BufferedReader(reader)

    def size_bytes(self, locator: str) -> int:
        try:
            head = _s3_client().head_object(Bucket=self._bucket, Key=locator)
        except Exception as exc:  # noqa: BLE001
            if _is_missing(exc):
                raise FileNotFoundError(f"s3://{self._bucket}/{locator}") from exc
            raise
        return int(head["ContentLength"])

    def presigned_put_url(self, locator: str, expires: int) -> str:
        # Content-Type is deliberately not signed: browsers set it from the
        # File and S3 doesn't enforce an unsigned header, so any content type
        # the browser sends is accepted.
        return _s3_client().generate_presigned_url(
            "put_object",
            Params={"Bucket": self._bucket, "Key": locator},
            ExpiresIn=expires,
        )

    # --- Multipart upload ---------------------------------------------------
    # For large objects on high-latency links a single PUT can't saturate the
    # uplink (one TCP stream is capped by its bandwidth-delay product). The
    # client slices the file and PUTs the parts in parallel to these presigned
    # URLs, then the server completes the upload from the collected ETags.
    def create_multipart(self, locator: str) -> str:
        resp = _s3_client().create_multipart_upload(
            Bucket=self._bucket, Key=locator
        )
        return resp["UploadId"]

    def presigned_upload_part_url(
        self, locator: str, upload_id: str, part_number: int, expires: int
    ) -> str:
        return _s3_client().generate_presigned_url(
            "upload_part",
            Params={
                "Bucket": self._bucket,
                "Key": locator,
                "UploadId": upload_id,
                "PartNumber": part_number,
            },
            ExpiresIn=expires,
        )

    def complete_multipart(self, locator: str, upload_id: str, parts: list) -> None:
        # ``parts`` is [{"PartNumber": int, "ETag": str}, ...], ascending.
        _s3_client().complete_multipart_upload(
            Bucket=self._bucket,
            Key=locator,
            UploadId=upload_id,
            MultipartUpload={"Parts": parts},
        )

    def abort_multipart(self, locator: str, upload_id: str) -> None:
        # Frees any already-uploaded parts. Best-effort: the bucket also has a
        # lifecycle rule that expires abandoned multipart uploads as a backstop.
        _s3_client().abort_multipart_upload(
            Bucket=self._bucket, Key=locator, UploadId=upload_id
        )

    def localize(self, locator: str, suffix: str = "") -> Path:
        fd, tmp = tempfile.mkstemp(suffix=suffix)
        os.close(fd)
        try:
            _s3_client().download_file(self._bucket, locator, tmp)
        except Exception as exc:  # noqa: BLE001
            Path(tmp).unlink(missing_ok=True)
            if _is_missing(exc):
                raise FileNotFoundError(f"s3://{self._bucket}/{locator}") from exc
            raise
        return Path(tmp)

    def delete(self, locator: str) -> None:
        # Delete every object under the locator (results/ is a "directory").
        client = _s3_client()
        keys = [{"Key": k} for k in self._list(locator)]
        if not keys:
            keys = [{"Key": locator}]
        for i in range(0, len(keys), 1000):
            client.delete_objects(
                Bucket=self._bucket, Delete={"Objects": keys[i : i + 1000]}
            )

    def _list(self, prefix: str):
        client = _s3_client()
        token = None
        while True:
            kw = {"Bucket": self._bucket, "Prefix": prefix}
            if token:
                kw["ContinuationToken"] = token
            resp = client.list_objects_v2(**kw)
            for obj in resp.get("Contents", []):
                yield obj["Key"]
            if not resp.get("IsTruncated"):
                return
            token = resp.get("NextContinuationToken")

    def spark_read_uri(self, locator: str) -> str:
        return f"s3a://{self._bucket}/{locator}"

    def spark_write_uri(self, locator: str) -> str:
        return f"s3a://{self._bucket}/{locator}"

    def parquet_glob(self, locator: str) -> str:
        return f"s3://{self._bucket}/{locator}/*.parquet"

    def spark_hadoop_conf(self) -> dict:
        conf = {
            "spark.hadoop.fs.s3a.impl": "org.apache.hadoop.fs.s3a.S3AFileSystem",
            # Default chain: env vars locally; ECS task / EC2 / IRSA role in AWS.
            "spark.hadoop.fs.s3a.aws.credentials.provider": (
                "com.amazonaws.auth.DefaultAWSCredentialsProviderChain"
            ),
            "spark.hadoop.fs.s3a.endpoint.region": settings.S3_REGION,
        }
        if settings.S3_ENDPOINT_URL:  # MinIO / LocalStack
            conf["spark.hadoop.fs.s3a.endpoint"] = settings.S3_ENDPOINT_URL
            conf["spark.hadoop.fs.s3a.path.style.access"] = "true"
        else:
            conf["spark.hadoop.fs.s3a.endpoint"] = f"s3.{settings.S3_REGION}.amazonaws.com"
        return conf

    def configure_duckdb(self, con) -> None:
        con.execute("INSTALL httpfs")
        con.execute("LOAD httpfs")
        parts = ["TYPE S3", "PROVIDER credential_chain", f"REGION '{settings.S3_REGION}'"]
        if settings.S3_ENDPOINT_URL:
            host = settings.S3_ENDPOINT_URL.split("://", 1)[-1]
            use_ssl = "true" if settings.S3_ENDPOINT_URL.startswith("https") else "false"
            parts += [f"ENDPOINT '{host}'", f"USE_SSL {use_ssl}", "URL_STYLE 'path'"]
        con.execute(f"CREATE SECRET s3_app ({', '.join(parts)})")


# --------------------------------------------------------------------------- #
# Backend selection + module-level facade
# --------------------------------------------------------------------------- #
def _backend():
    if getattr(settings, "STORAGE_BACKEND", "local") == "s3":
        return _S3Backend()
    return _LocalBackend()


def backend_name() -> str:
    return _backend().name


def upload_locator(file_id, suffix: str) -> str:
    """Locator for a newly uploaded file."""
    return _backend().upload_locator(file_id, suffix)


def result_locator(job_id) -> str:
    """Locator (directory/prefix) Spark writes the Parquet result into."""
    return _backend().result_locator(job_id)


def persist_upload(src: Path, locator: str) -> int:
    """Store the staged local file ``src`` at ``locator``; return its size."""
    return _backend().persist_upload(src, locator)


def staging_path(suffix: str) -> Path:
    """A local temp path to stream an upload into before persisting it."""
    return _backend().staging_path(suffix)


def open_binary(locator: str):
    """Open a seekable binary stream for reading (raises FileNotFoundError)."""
    return _backend().open_binary(locator)


def size_bytes(locator: str) -> int:
    """Size in bytes of the object at ``locator`` (raises FileNotFoundError)."""
    return _backend().size_bytes(locator)


def direct_upload_supported() -> bool:
    """True if a client can upload straight to storage via a presigned URL.

    Only the S3 backend can; the local backend has no browser-reachable target,
    so callers fall back to a normal multipart POST through the web process.
    """
    return backend_name() == "s3"


def presigned_put_url(locator: str, expires: int = 3600) -> str:
    """A time-limited URL the browser can ``PUT`` an upload straight to."""
    return _backend().presigned_put_url(locator, expires)


def create_multipart(locator: str) -> str:
    """Begin a multipart upload at ``locator``; return the S3 upload id."""
    return _backend().create_multipart(locator)


def presigned_upload_part_url(
    locator: str, upload_id: str, part_number: int, expires: int = 3600
) -> str:
    """A time-limited URL the browser can ``PUT`` one part of a multipart upload to."""
    return _backend().presigned_upload_part_url(
        locator, upload_id, part_number, expires
    )


def complete_multipart(locator: str, upload_id: str, parts: list) -> None:
    """Assemble the uploaded parts into the final object (parts ascending)."""
    _backend().complete_multipart(locator, upload_id, parts)


def abort_multipart(locator: str, upload_id: str) -> None:
    """Discard an in-progress multipart upload and its already-uploaded parts."""
    _backend().abort_multipart(locator, upload_id)


def localize(locator: str, suffix: str = "") -> Path:
    """Return a local filesystem Path for ``locator`` (downloads from S3)."""
    return _backend().localize(locator, suffix)


def delete(locator: str) -> None:
    """Delete the object(s) at ``locator``. Best-effort, never raises on 404."""
    _backend().delete(locator)


def spark_read_uri(locator: str) -> str:
    """URI Spark reads the source from (``s3a://…`` or a local path)."""
    return _backend().spark_read_uri(locator)


def spark_write_uri(locator: str) -> str:
    """URI Spark writes the result to (``s3a://…`` or a local path)."""
    return _backend().spark_write_uri(locator)


def parquet_glob(locator: str) -> str:
    """DuckDB glob over the result Parquet files (``s3://…`` or a local path)."""
    return _backend().parquet_glob(locator)


def spark_hadoop_conf() -> dict:
    """Spark ``spark.hadoop.*`` settings needed for the active backend."""
    return _backend().spark_hadoop_conf()


def configure_duckdb(con) -> None:
    """Prepare a DuckDB connection to read the active backend (httpfs + creds)."""
    _backend().configure_duckdb(con)
