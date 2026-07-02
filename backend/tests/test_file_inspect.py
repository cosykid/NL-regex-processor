from pathlib import Path

from jobs.models import UploadedFile
from processing.file_inspect import (
    detect_kind,
    inspect,
    read_window,
    sample_values,
)


def test_detect_kind():
    assert detect_kind("data.csv") == UploadedFile.Kind.CSV
    assert detect_kind("report.XLSX") == UploadedFile.Kind.EXCEL
    assert detect_kind("noext") == UploadedFile.Kind.CSV


def test_inspect_csv(tmp_path: Path):
    p = tmp_path / "people.csv"
    p.write_text(
        "ID,Name,Email\n"
        "1,John Doe,john.doe@example.com\n"
        "2,Jane Smith,jane_smith@domain.com\n",
        encoding="utf-8",
    )
    info = inspect(p, UploadedFile.Kind.CSV)
    assert info["columns"] == ["ID", "Name", "Email"]
    assert len(info["preview_rows"]) == 2
    assert info["preview_rows"][0]["Email"] == "john.doe@example.com"


def test_inspect_excel(tmp_path: Path):
    from openpyxl import Workbook

    wb = Workbook()
    ws = wb.active
    ws.append(["ID", "Name", "Email"])
    ws.append([1, "John Doe", "john.doe@example.com"])
    p = tmp_path / "people.xlsx"
    wb.save(p)

    info = inspect(p, UploadedFile.Kind.EXCEL)
    assert info["columns"] == ["ID", "Name", "Email"]
    assert info["preview_rows"][0]["Name"] == "John Doe"


def _write_numbered_csv(path: Path, n: int) -> list[str]:
    cols = ["ID", "Val"]
    lines = [",".join(cols)] + [f"{i},v{i}" for i in range(1, n + 1)]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return cols


def test_read_window_csv_paging(tmp_path: Path):
    p = tmp_path / "nums.csv"
    cols = _write_numbered_csv(p, 5)  # 5 data rows
    CSV = UploadedFile.Kind.CSV

    first = read_window(p, CSV, cols, cursor=None, limit=2)
    assert [r["ID"] for r in first["rows"]] == ["1", "2"]
    assert first["eof"] is False
    assert first["cursor"]  # opaque continuation token

    mid = read_window(p, CSV, cols, cursor=first["cursor"], limit=2)
    assert [r["ID"] for r in mid["rows"]] == ["3", "4"]
    assert mid["eof"] is False

    # A short final window reports EOF (and no cursor) so the caller stops.
    last = read_window(p, CSV, cols, cursor=mid["cursor"], limit=2)
    assert [r["ID"] for r in last["rows"]] == ["5"]
    assert last["eof"] is True
    assert last["cursor"] is None


def test_read_window_csv_cursor_is_byte_offset(tmp_path: Path):
    # The whole point of the cursor: resuming is a seek, not a rescan. After the
    # first window the cursor should be a byte offset well past the header.
    p = tmp_path / "nums.csv"
    cols = _write_numbered_csv(p, 100)
    first = read_window(p, UploadedFile.Kind.CSV, cols, cursor=None, limit=10)
    assert int(first["cursor"]) > 0
    resumed = read_window(
        p, UploadedFile.Kind.CSV, cols, cursor=first["cursor"], limit=10
    )
    assert [r["ID"] for r in resumed["rows"]] == [str(i) for i in range(11, 21)]


def test_read_window_csv_quoted_newline(tmp_path: Path):
    # A quoted field spanning a newline must not desync the byte cursor.
    p = tmp_path / "q.csv"
    p.write_text('ID,Note\n1,"line one\nline two"\n2,ok\n', encoding="utf-8")
    cols = ["ID", "Note"]

    first = read_window(p, UploadedFile.Kind.CSV, cols, cursor=None, limit=1)
    assert first["rows"][0]["Note"] == "line one\nline two"
    assert first["eof"] is False

    nxt = read_window(p, UploadedFile.Kind.CSV, cols, cursor=first["cursor"], limit=1)
    assert [r["ID"] for r in nxt["rows"]] == ["2"]
    assert nxt["eof"] is True


def test_sample_values_dedupes_and_preserves_case():
    # The Railway-column bug: values are "False"/"True", so the samples handed
    # to the LLM must carry that exact case (not a lower-cased guess).
    preview = [
        {"Railway": "False", "Weather": "Fair"},
        {"Railway": "True", "Weather": "Cloudy"},
        {"Railway": "False", "Weather": "Fair"},  # duplicate row values
    ]
    out = sample_values(preview, ["Railway"], per_column=8, max_len=80)
    assert out == {"Railway": ["False", "True"]}  # distinct, first-seen order


def test_sample_values_caps_count():
    preview = [{"C": v} for v in ["a", "b", "c", "d", "e"]]
    out = sample_values(preview, ["C"], per_column=3, max_len=80)
    assert out["C"] == ["a", "b", "c"]  # first 3 distinct values only


def test_sample_values_truncates_long_values():
    preview = [{"C": "x" * 100}]
    out = sample_values(preview, ["C"], per_column=8, max_len=5)
    assert out["C"] == ["xxxxx"]  # capped to max_len characters


def test_sample_values_spreads_across_the_window():
    # 20 distinct rows, want 10 -> stride 2: samples are drawn from across the
    # whole window (every other row), not clustered in the first 10.
    preview = [{"C": f"v{i}"} for i in range(20)]
    out = sample_values(preview, ["C"], per_column=10, max_len=80)
    assert out["C"] == [f"v{i}" for i in range(0, 20, 2)]
    assert out["C"] != [f"v{i}" for i in range(10)]  # not the top-10


def test_sample_values_skips_empty_and_missing_columns():
    preview = [
        {"A": "", "B": "keep"},
        {"A": "   ", "B": None},  # whitespace-only and None are not samples
    ]
    out = sample_values(preview, ["A", "B", "Absent"], per_column=8, max_len=80)
    # A has only blank values, Absent isn't in any row -> both omitted entirely.
    assert out == {"B": ["keep"]}


def test_read_window_excel_paging(tmp_path: Path):
    from openpyxl import Workbook

    wb = Workbook()
    ws = wb.active
    ws.append(["ID", "Val"])
    for i in range(1, 6):
        ws.append([i, f"v{i}"])
    p = tmp_path / "nums.xlsx"
    wb.save(p)
    EXCEL = UploadedFile.Kind.EXCEL

    win = read_window(p, EXCEL, ["ID", "Val"], cursor=None, limit=2)
    assert [r["ID"] for r in win["rows"]] == ["1", "2"]
    assert win["eof"] is False

    mid = read_window(p, EXCEL, ["ID", "Val"], cursor=win["cursor"], limit=2)
    assert [r["ID"] for r in mid["rows"]] == ["3", "4"]
    assert mid["eof"] is False

    tail = read_window(p, EXCEL, ["ID", "Val"], cursor=mid["cursor"], limit=2)
    assert [r["ID"] for r in tail["rows"]] == ["5"]
    assert tail["eof"] is True
    assert tail["cursor"] is None
