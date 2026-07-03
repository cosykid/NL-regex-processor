"""Paged reads over Parquet results (`processing.results.read_page`).

Exercises the reader directly (no Spark, no DB): a small multi-row Parquet with
the internal match-flag column stands in for a real result. Covers the full-view
fast path (row numbers computed as offset+position, no global window), the
affected-only path (window preserves each row's original full-view index), and
the caller-supplied count reuse.
"""
import duckdb
import pytest

from processing import results
from processing.spark_engine import MATCH_FLAG_COLUMN


@pytest.fixture
def result_dir(tmp_path):
    """A one-file Parquet 'result': 5 rows, flags at original rows 2 and 4."""
    d = tmp_path / "result"
    d.mkdir()
    con = duckdb.connect()
    con.execute(
        f"""
        COPY (
          SELECT * FROM (VALUES
            ('r1', FALSE),
            ('r2', TRUE),
            ('r3', FALSE),
            ('r4', TRUE),
            ('r5', FALSE)
          ) t(name, "{MATCH_FLAG_COLUMN}")
        ) TO '{d}/part.parquet' (FORMAT PARQUET)
        """
    )
    con.close()
    return str(d)


def test_full_view_numbers_rows_from_offset(result_dir):
    out = results.read_page(result_dir, page=1, page_size=2)
    assert [r["name"] for r in out["rows"]] == ["r1", "r2"]
    assert [r["__rownum"] for r in out["rows"]] == [1, 2]
    assert out["columns"] == ["name"]  # flag column hidden
    assert out["total"] == 5
    assert out["num_pages"] == 3


def test_full_view_second_page_continues_numbering(result_dir):
    out = results.read_page(result_dir, page=2, page_size=2)
    assert [r["name"] for r in out["rows"]] == ["r3", "r4"]
    assert [r["__rownum"] for r in out["rows"]] == [3, 4]


def test_affected_only_keeps_original_full_view_index(result_dir):
    out = results.read_page(result_dir, page=1, page_size=10, matched_only=True)
    assert [r["name"] for r in out["rows"]] == ["r2", "r4"]
    # Original (full-view) positions, not 1,2 of the filtered subset.
    assert [r["__rownum"] for r in out["rows"]] == [2, 4]
    assert out["total"] == 2
    assert out["matched_only"] is True


def test_match_flag_surfaced_and_stripped(result_dir):
    out = results.read_page(result_dir, page=1, page_size=10)
    assert [r["__matched__"] for r in out["rows"]] == [False, True, False, True, False]
    assert MATCH_FLAG_COLUMN not in out["rows"][0]


def test_supplied_counts_are_used_verbatim(result_dir):
    # A (count reuse): passed totals are returned as-is, not recomputed from the
    # parquet — deliberately "wrong" values prove the scan was skipped.
    out = results.read_page(
        result_dir, page=1, page_size=2, total_all=999, matched_total=7
    )
    assert out["total_all"] == 999
    assert out["matched_total"] == 7
    assert out["total"] == 999  # full view -> total_all
    assert out["num_pages"] == 500  # ceil(999 / 2)


def test_supplied_matched_total_used_when_filtered(result_dir):
    out = results.read_page(
        result_dir, page=1, page_size=10, matched_only=True,
        total_all=999, matched_total=7,
    )
    assert out["total"] == 7  # filtered -> matched_total
