"""Shared pytest fixtures."""
import pytest


@pytest.fixture(autouse=True)
def _local_storage_backend(settings):
    """Pin the local storage backend for the whole suite.

    Tests write/read files on the local filesystem; without this they'd inherit
    the ambient ``STORAGE_BACKEND`` (a dev/CI shell may export ``s3``), and the
    result reader would rewrite local temp paths into ``s3://…`` globs and fail —
    or, worse, touch a real bucket. Tests that specifically exercise the S3
    mapping override this in their own fixtures.
    """
    settings.STORAGE_BACKEND = "local"
