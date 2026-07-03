"""Paged reads and exports of Spark-written Parquet results.

Reading runs in the *web* process and must stay light — we never load the whole
result into memory or boot Spark just to serve a page. DuckDB reads only the
Parquet row groups needed to satisfy ``LIMIT/OFFSET``, so paging a
million-row result is cheap, and a CSV export streams straight from Parquet.
"""
from __future__ import annotations

import os
import re
import tempfile
from contextlib import contextmanager
from datetime import date, datetime, time
from decimal import Decimal

from . import storage
from .spark_engine import MATCH_FLAG_COLUMN

# Control chars the OOXML spec forbids in a cell — everything in C0 except tab
# (\x09), newline (\x0a) and CR (\x0d). Mirrors openpyxl's own guard so we can
# strip offending bytes without importing openpyxl at module load.
_ILLEGAL_XLSX_CHARS = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f]")

# A single .xlsx worksheet caps at 1,048,576 rows *including* the header,
# 16,384 columns, and 32,767 characters per cell. Results can exceed the row and
# column ceilings, so we fail an Excel export loudly rather than silently drop
# data — CSV has no such limits.
XLSX_MAX_ROWS = 1_048_576
XLSX_MAX_COLS = 16_384
XLSX_MAX_CELL_LEN = 32_767

# Per-row match state is surfaced to the client under this stable key (the
# internal Parquet column name is an implementation detail).
ROW_MATCH_KEY = "__matched__"


def _glob(result_path: str) -> str:
    return storage.parquet_glob(result_path)


def _schema_columns(con, glob: str) -> list[str]:
    """Column names of the result, in file order (no rows read)."""
    desc = con.execute("SELECT * FROM read_parquet(?) LIMIT 0", [glob]).description
    return [d[0] for d in desc]


@contextmanager
def _open_result(glob: str):
    """A configured DuckDB connection plus the result's column split.

    Every reader/exporter needs the same preamble: connect, wire up the storage
    backend (httpfs + creds when reading from S3), read the schema, and separate
    the internal match-flag column from the user-facing ones. Yields
    ``(con, has_flag, display_cols)`` and always closes the connection.
    """
    import duckdb

    con = duckdb.connect()
    try:
        storage.configure_duckdb(con)
        all_cols = _schema_columns(con, glob)
        has_flag = MATCH_FLAG_COLUMN in all_cols
        display_cols = [c for c in all_cols if c != MATCH_FLAG_COLUMN]
        yield con, has_flag, display_cols
    finally:
        con.close()


def read_page(
    result_path: str,
    page: int,
    page_size: int,
    matched_only: bool = False,
    *,
    total_all: int | None = None,
    matched_total: int | None = None,
) -> dict:
    """Read one page of a result.

    ``total_all`` / ``matched_total`` are the row counts the caller already has
    persisted on the finished ``Job`` (``total_rows`` / ``matched_rows``). When
    given, we skip recomputing them here — the ``count(*) WHERE flag`` scan in
    particular is a per-request full-column scan we don't need to repeat. Pass
    ``None`` to compute them from the parquet (standalone / legacy callers).
    """
    glob = _glob(result_path)
    page = max(1, page)
    page_size = max(1, min(page_size, 500))
    offset = (page - 1) * page_size

    with _open_result(glob) as (con, has_flag, display_cols):
        # Counts don't change for a finished result — reuse the caller's persisted
        # values when supplied, else fall back to computing them.
        if total_all is None:
            total_all = con.execute(
                "SELECT count(*) FROM read_parquet(?)", [glob]
            ).fetchone()[0]
        if matched_total is None:
            matched_total = (
                con.execute(
                    f'SELECT count(*) FROM read_parquet(?) WHERE "{MATCH_FLAG_COLUMN}"',
                    [glob],
                ).fetchone()[0]
                if has_flag
                else 0
            )

        filtered = bool(matched_only and has_flag)
        total = matched_total if filtered else total_all

        # Row ordering is deterministic across any thread count via the parquet
        # scan position (filename, then row within file), rather than relying on
        # single-threaded scan order.
        if filtered:
            # Affected-only: a displayed row's number must be its index in the
            # FULL result, not the filtered subset — so number every row first,
            # THEN filter/slice. The global window is unavoidable here.
            rel = con.execute(
                "WITH numbered AS ("
                "  SELECT * EXCLUDE (filename, file_row_number),"
                "         row_number() OVER (ORDER BY filename, file_row_number) AS __rownum"
                "  FROM read_parquet(?, filename=true, file_row_number=true)"
                f') SELECT * FROM numbered WHERE "{MATCH_FLAG_COLUMN}"'
                " ORDER BY __rownum LIMIT ? OFFSET ?",
                [glob, page_size, offset],
            )
            cols = [d[0] for d in rel.description]
            raw_rows = [dict(zip(cols, r)) for r in rel.fetchall()]
        else:
            # Full view: the page's sort order IS the numbering order, so each
            # row's number is just ``offset + its position on the page``. Skip the
            # global window entirely — DuckDB does a top-(offset+limit) sort and
            # stops, instead of numbering every row only to discard all but one
            # page. This is the dominant per-page cost on large results.
            rel = con.execute(
                "SELECT * EXCLUDE (filename, file_row_number)"
                " FROM read_parquet(?, filename=true, file_row_number=true)"
                " ORDER BY filename, file_row_number LIMIT ? OFFSET ?",
                [glob, page_size, offset],
            )
            cols = [d[0] for d in rel.description]
            raw_rows = []
            for j, r in enumerate(rel.fetchall()):
                row = dict(zip(cols, r))
                row["__rownum"] = offset + j + 1
                raw_rows.append(row)

    rows = []
    for r in raw_rows:
        matched = bool(r.pop(MATCH_FLAG_COLUMN, False))
        rownum = r.pop("__rownum", None)
        r[ROW_MATCH_KEY] = matched
        if rownum is not None:
            r["__rownum"] = int(rownum)
        rows.append(r)

    return {
        "columns": display_cols,
        "rows": rows,
        "total": total,
        "total_all": total_all,
        "matched_total": matched_total,
        "has_match_flag": has_flag,
        "matched_only": filtered,
        "page": page,
        "page_size": page_size,
        "num_pages": max(1, -(-total // page_size)),
    }


def write_csv(result_path: str, matched_only: bool = False) -> str:
    """Materialise the result (optionally affected-only) to a temp CSV file.

    Streams Parquet -> CSV inside DuckDB without loading it into Python; the
    internal match-flag column is dropped. Returns the temp file path — the
    caller streams it to the client and deletes it.
    """
    glob = _glob(result_path)
    fd, tmp = tempfile.mkstemp(suffix=".csv")
    os.close(fd)

    try:
        with _open_result(glob) as (con, has_flag, display_cols):
            col_list = ", ".join(f'"{c}"' for c in display_cols) or "*"
            where = f' WHERE "{MATCH_FLAG_COLUMN}"' if (matched_only and has_flag) else ""

            # `glob` and `tmp` are server-derived (a UUID result dir + mkstemp
            # path), not user input; quote-escape defensively all the same.
            glob_lit = glob.replace("'", "''")
            tmp_lit = tmp.replace("'", "''")
            con.execute(
                f"COPY (SELECT {col_list} FROM read_parquet('{glob_lit}'){where}) "
                f"TO '{tmp_lit}' (HEADER, FORMAT CSV)"
            )
    except Exception:
        os.unlink(tmp)
        raise

    return tmp


class ResultTooLargeForExcel(ValueError):
    """The result has more rows than a single .xlsx sheet can hold."""


def _xlsx_cell(value):
    """Coerce a DuckDB value to something openpyxl will accept.

    Native scalars (numbers, bools, dates) pass through so Excel keeps their
    type; everything else is stringified. Strings are stripped of the control
    characters Excel rejects and clamped to the per-cell length ceiling.
    """
    if value is None or isinstance(value, (int, float, Decimal, datetime, date, time)):
        return value
    text = value if isinstance(value, str) else str(value)
    # openpyxl raises IllegalCharacterError on the C0 control range (bar tab /
    # newline / CR); drop them so one stray byte can't sink the whole export.
    text = _ILLEGAL_XLSX_CHARS.sub("", text)
    if len(text) > XLSX_MAX_CELL_LEN:
        text = text[:XLSX_MAX_CELL_LEN]
    return text


def write_xlsx(result_path: str, matched_only: bool = False) -> str:
    """Materialise the result (optionally affected-only) to a temp .xlsx file.

    Streams Parquet -> rows via DuckDB and appends them with openpyxl in
    write-only mode, so memory stays bounded even for large results. The
    internal match-flag column is dropped. Returns the temp file path — the
    caller streams it to the client and deletes it.

    Raises ``ResultTooLargeForExcel`` if the row or column count exceeds what a
    worksheet can hold; the caller should steer the user to CSV.
    """
    from openpyxl import Workbook

    glob = _glob(result_path)

    with _open_result(glob) as (con, has_flag, display_cols):
        col_list = ", ".join(f'"{c}"' for c in display_cols) or "*"
        where = f' WHERE "{MATCH_FLAG_COLUMN}"' if (matched_only and has_flag) else ""

        # Column check is free (schema only) — do it before touching any rows.
        if len(display_cols) > XLSX_MAX_COLS:
            raise ResultTooLargeForExcel(
                f"This result has {len(display_cols):,} columns, which is more "
                f"than Excel's limit of {XLSX_MAX_COLS:,} columns per sheet. "
                "Export as CSV instead."
            )

        total = con.execute(
            f"SELECT count(*) FROM read_parquet(?){where}", [glob]
        ).fetchone()[0]
        if total > XLSX_MAX_ROWS - 1:  # -1 reserves the header row
            raise ResultTooLargeForExcel(
                f"This result has {total:,} rows, which is more than Excel's "
                f"limit of {XLSX_MAX_ROWS - 1:,} data rows per sheet. "
                "Export as CSV instead."
            )

        rel = con.execute(f"SELECT {col_list} FROM read_parquet(?){where}", [glob])

        fd, tmp = tempfile.mkstemp(suffix=".xlsx")
        os.close(fd)
        try:
            wb = Workbook(write_only=True)
            ws = wb.create_sheet("Results")
            ws.append(display_cols)
            while True:
                batch = rel.fetchmany(10_000)
                if not batch:
                    break
                for row in batch:
                    ws.append([_xlsx_cell(v) for v in row])
            wb.save(tmp)
        except Exception:
            os.unlink(tmp)
            raise

    return tmp
