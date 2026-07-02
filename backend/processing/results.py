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


def read_page(
    result_path: str, page: int, page_size: int, matched_only: bool = False
) -> dict:
    import duckdb

    glob = _glob(result_path)
    page = max(1, page)
    page_size = max(1, min(page_size, 500))
    offset = (page - 1) * page_size

    con = duckdb.connect()
    try:
        storage.configure_duckdb(con)  # httpfs + creds when reading from S3
        # Single-threaded so row_number() follows the parquet scan order
        # deterministically — this is what lets a row keep its full-view number
        # when the affected-only filter is applied.
        con.execute("SET threads TO 1")

        all_cols = _schema_columns(con, glob)
        has_flag = MATCH_FLAG_COLUMN in all_cols
        display_cols = [c for c in all_cols if c != MATCH_FLAG_COLUMN]

        total_all = con.execute(
            "SELECT count(*) FROM read_parquet(?)", [glob]
        ).fetchone()[0]
        matched_total = (
            con.execute(
                f'SELECT count(*) FROM read_parquet(?) WHERE "{MATCH_FLAG_COLUMN}"',
                [glob],
            ).fetchone()[0]
            if has_flag
            else 0
        )

        filtered = bool(matched_only and has_flag)
        where = f' WHERE "{MATCH_FLAG_COLUMN}"' if filtered else ""
        total = matched_total if filtered else total_all

        # Number every row over the full result first, THEN filter/slice, so an
        # affected-only page still reports each row's original (full-view) index.
        rel = con.execute(
            "WITH numbered AS ("
            "  SELECT *, row_number() OVER () AS __rownum FROM read_parquet(?)"
            f") SELECT * FROM numbered{where} LIMIT ? OFFSET ?",
            [glob, page_size, offset],
        )
        cols = [d[0] for d in rel.description]
        raw_rows = [dict(zip(cols, r)) for r in rel.fetchall()]
    finally:
        con.close()

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
    import duckdb

    glob = _glob(result_path)
    fd, tmp = tempfile.mkstemp(suffix=".csv")
    os.close(fd)

    con = duckdb.connect()
    try:
        storage.configure_duckdb(con)  # httpfs + creds when reading from S3
        all_cols = _schema_columns(con, glob)
        has_flag = MATCH_FLAG_COLUMN in all_cols
        display_cols = [c for c in all_cols if c != MATCH_FLAG_COLUMN]
        col_list = ", ".join(f'"{c}"' for c in display_cols) or "*"
        where = f' WHERE "{MATCH_FLAG_COLUMN}"' if (matched_only and has_flag) else ""

        # `glob` and `tmp` are server-derived (a UUID result dir + mkstemp path),
        # not user input; quote-escape defensively all the same.
        glob_lit = glob.replace("'", "''")
        tmp_lit = tmp.replace("'", "''")
        con.execute(
            f"COPY (SELECT {col_list} FROM read_parquet('{glob_lit}'){where}) "
            f"TO '{tmp_lit}' (HEADER, FORMAT CSV)"
        )
    except Exception:
        os.unlink(tmp)
        raise
    finally:
        con.close()

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
    import duckdb
    from openpyxl import Workbook

    glob = _glob(result_path)

    con = duckdb.connect()
    try:
        storage.configure_duckdb(con)  # httpfs + creds when reading from S3
        all_cols = _schema_columns(con, glob)
        has_flag = MATCH_FLAG_COLUMN in all_cols
        display_cols = [c for c in all_cols if c != MATCH_FLAG_COLUMN]
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
    finally:
        con.close()

    return tmp
