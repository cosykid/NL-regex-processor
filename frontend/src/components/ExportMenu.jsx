import { useEffect, useRef, useState } from "react";
import { exportUrl } from "../api";
import { fmtInt } from "../util";

// Excel worksheet ceilings — mirror the backend guard in processing/results.py.
// A sheet holds 1,048,576 rows *including* the header (so 1,048,575 data rows)
// and 16,384 columns. Past either, an .xlsx can't hold the result; CSV can.
const XLSX_MAX_DATA_ROWS = 1_048_576 - 1;
const XLSX_MAX_COLS = 16_384;

function DownloadIcon() {
  return (
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
      <path
        d="M12 3v12m0 0 4-4m-4 4-4-4"
        strokeLinecap="round"
        strokeLinejoin="round"
      />
      <path d="M4 17v2a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2v-2" strokeLinecap="round" />
    </svg>
  );
}

/**
 * Download the current result as CSV or Excel. A button opens a small menu
 * whose items are real <a download> links, so the browser streams the file
 * straight from the API (the same request path the single CSV button used).
 *
 * Excel can't hold a result past its row/column ceilings, so when the export
 * scope (which honours the affected-only toggle) exceeds either cap we disable
 * the .xlsx option up front — the user never triggers a download that the
 * backend would only reject — and point them to CSV, which has no such limit.
 */
export default function ExportMenu({
  jobId,
  affectedOnly,
  totalRows = 0,
  matchedRows = 0,
  columnCount = 0,
}) {
  const [open, setOpen] = useState(false);
  const ref = useRef(null);

  // Close on outside click / Escape while the menu is open.
  useEffect(() => {
    if (!open) return;
    const onDown = (e) => {
      if (ref.current && !ref.current.contains(e.target)) setOpen(false);
    };
    const onKey = (e) => e.key === "Escape" && setOpen(false);
    document.addEventListener("mousedown", onDown);
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("mousedown", onDown);
      document.removeEventListener("keydown", onKey);
    };
  }, [open]);

  const scope = affectedOnly ? " affected" : "";

  // Rows that would actually be written, given the affected-only toggle.
  const exportRows = affectedOnly ? matchedRows : totalRows;
  const reasons = [];
  if (exportRows > XLSX_MAX_DATA_ROWS) {
    reasons.push(
      `${fmtInt(exportRows)} rows exceeds Excel's ${fmtInt(XLSX_MAX_DATA_ROWS)}`
    );
  }
  if (columnCount > XLSX_MAX_COLS) {
    reasons.push(
      `${fmtInt(columnCount)} columns exceeds Excel's ${fmtInt(XLSX_MAX_COLS)}`
    );
  }
  const xlsxBlocked = reasons.length > 0;
  const xlsxReason = xlsxBlocked ? `${reasons.join("; ")}. Export as CSV instead.` : "";

  const item = (format, label, { disabled = false, title } = {}) =>
    disabled ? (
      <span
        className="export-item disabled"
        role="menuitem"
        aria-disabled="true"
        title={title}
      >
        {label}
      </span>
    ) : (
      <a
        className="export-item"
        role="menuitem"
        href={exportUrl(jobId, { matchedOnly: affectedOnly, format })}
        download
        title={title}
        onClick={() => setOpen(false)}
      >
        {label}
      </a>
    );

  return (
    <div className="export-menu" ref={ref}>
      <button
        type="button"
        className="export-btn"
        aria-haspopup="menu"
        aria-expanded={open}
        onClick={() => setOpen((o) => !o)}
        title={`Download${scope || " all"} rows`}
      >
        <DownloadIcon />
        Export{scope}
        <svg
          className="export-caret"
          viewBox="0 0 24 24"
          fill="none"
          stroke="currentColor"
          strokeWidth="2"
          aria-hidden="true"
        >
          <path d="m6 9 6 6 6-6" strokeLinecap="round" strokeLinejoin="round" />
        </svg>
      </button>
      {open && (
        <div className="export-pop" role="menu">
          {item("csv", "CSV (.csv)")}
          {item("xlsx", "Excel (.xlsx)", {
            disabled: xlsxBlocked,
            title: xlsxReason || undefined,
          })}
          {xlsxBlocked && <p className="export-hint">Too big for Excel — use CSV</p>}
        </div>
      )}
    </div>
  );
}
