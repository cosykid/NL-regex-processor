import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { getResults, getUploadRows } from "../api";
import { TERMINAL, fmtInt, inferType, prettyStage } from "../util";
import DataGrid from "./DataGrid";
import ExportMenu from "./ExportMenu";
import RowDetail from "./RowDetail";

export const PAGE_SIZE = 200;
// Rows fetched per lazy window when scrolling the original (pre-transform) file.
const PREVIEW_WINDOW = 100;

/**
 * Owns the grid viewport. With no run selected it shows the dataset's original
 * data and lazily loads more rows as you scroll — you can browse the whole file
 * before applying any transformation, not just the initial preview. With a
 * successful run it pages through the result a window at a time — Prev/Next or a
 * direct jump-to-row, so any row in a million-row result is one step away
 * without scrolling through everything. Rows the pattern affected are
 * emphasised, and can be isolated with an affected-only view. Running / failed /
 * cancelled runs render the original as a backdrop behind a state overlay.
 */
export default function GridArea({
  dataset,
  activeRun,
  targets,
  onToggleTarget,
  onMeta,
  selectedCell,
  onSelectCell,
  onCancel,
}) {
  const showingResult = activeRun && activeRun.status === "SUCCESS";

  const [columns, setColumns] = useState(null);
  const [rows, setRows] = useState([]);
  const [total, setTotal] = useState(0); // rows in the current view
  const [totalAll, setTotalAll] = useState(0);
  const [matchedTotal, setMatchedTotal] = useState(0);
  const [hasFlag, setHasFlag] = useState(false);
  const [affectedOnly, setAffectedOnly] = useState(false);
  const [page, setPage] = useState(1);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const [jumpText, setJumpText] = useState("");
  const [flashIndex, setFlashIndex] = useState(null);
  const [openRow, setOpenRow] = useState(null); // row index (in page) shown in detail panel

  // Original-file browsing (no run selected): rows accumulate as you scroll.
  const [previewRows, setPreviewRows] = useState([]);
  const [previewDone, setPreviewDone] = useState(false); // reached end of file
  const [previewLoading, setPreviewLoading] = useState(false);
  const [previewError, setPreviewError] = useState("");

  const scrollRef = useRef(null);
  const sentinelRef = useRef(null); // bottom marker that drives lazy loading
  const previewCursorRef = useRef(null); // opaque continuation cursor from the API
  const previewStartedRef = useRef(false); // has the first real window superseded the seed?
  const previewLoadingRef = useRef(false); // guards against overlapping windows
  const reqToken = useRef(0); // invalidates in-flight requests on run/page switch
  const pendingFlash = useRef(null); // row index to flash once a page lands
  const flashTimer = useRef(null);

  // Only the pure "no run selected" state browses the original file; a running
  // or failed run shows the preview as a backdrop behind an overlay, so we must
  // not fetch more there.
  const browsingOriginal = !activeRun;

  const numPages = Math.max(1, Math.ceil(total / PAGE_SIZE));
  const rowOffset = (page - 1) * PAGE_SIZE;

  /* ---- reset when the active result changes ---- */
  useEffect(() => {
    setAffectedOnly(false);
    setPage(1);
    setJumpText("");
    pendingFlash.current = null;
  }, [activeRun?.id]);

  /* ---- seed the original-file view from the upload's initial preview ---- */
  useEffect(() => {
    setPreviewRows(dataset?.preview_rows ?? []);
    setPreviewDone(false);
    setPreviewError("");
    previewCursorRef.current = null;
    previewStartedRef.current = false;
    previewLoadingRef.current = false;
    setPreviewLoading(false);
  }, [dataset?.id]);

  // Fetch the next window of the original file. Continuation is cursor-based, so
  // each fetch reads only its own window rather than re-scanning from the top.
  // Guarded with a ref so a burst of scroll events can't fire overlapping calls.
  const loadMorePreview = useCallback(() => {
    if (!dataset || previewLoadingRef.current || previewDone) return;
    previewLoadingRef.current = true;
    setPreviewLoading(true);
    getUploadRows(dataset.id, previewCursorRef.current, PREVIEW_WINDOW)
      .then((d) => {
        const incoming = d.rows ?? [];
        // The first real window is read from the top and supersedes the seed
        // preview; every window after it appends.
        if (previewStartedRef.current) {
          setPreviewRows((prev) => [...prev, ...incoming]);
        } else {
          previewStartedRef.current = true;
          setPreviewRows(incoming);
        }
        previewCursorRef.current = d.cursor ?? null;
        if (d.eof || !d.cursor) setPreviewDone(true);
      })
      .catch((e) => {
        setPreviewError(e.message);
        setPreviewDone(true); // stop retrying a failing window
      })
      .finally(() => {
        previewLoadingRef.current = false;
        setPreviewLoading(false);
      });
  }, [dataset, previewDone]);

  const flashAndScroll = useCallback((idx) => {
    const root = scrollRef.current;
    if (!root || idx == null) return;
    const el = root.querySelector(`tr[data-ri="${idx}"]`);
    if (el) el.scrollIntoView({ block: "center", behavior: "smooth" });
    setFlashIndex(idx);
    clearTimeout(flashTimer.current);
    flashTimer.current = setTimeout(() => setFlashIndex(null), 1700);
  }, []);

  /* ---- (re)load whenever the result, page, or filter changes ---- */
  useEffect(() => {
    const token = ++reqToken.current;
    setOpenRow(null); // a new set of rows invalidates the open detail

    if (!showingResult) {
      // Clear any stale result state; the original-file view owns its own rows
      // and reports its own meta (see the preview effects below).
      setColumns(null);
      setRows([]);
      setTotal(0);
      setTotalAll(0);
      setMatchedTotal(0);
      setHasFlag(false);
      setError("");
      setLoading(false);
      return;
    }

    setLoading(true);
    setError("");

    getResults(activeRun.id, page, PAGE_SIZE, affectedOnly)
      .then((d) => {
        if (token !== reqToken.current) return;
        setColumns(d.columns);
        setRows(d.rows);
        setTotal(d.total);
        setTotalAll(d.total_all ?? d.total);
        setMatchedTotal(d.matched_total ?? 0);
        setHasFlag(!!d.has_match_flag);
        onMeta?.({
          preview: false,
          total: d.total,
          totalAll: d.total_all ?? d.total,
          matchedTotal: d.matched_total ?? 0,
          affectedOnly,
          shown: d.rows.length,
          page: d.page,
          numPages: d.num_pages,
        });
        if (scrollRef.current) scrollRef.current.scrollTop = 0;
        // If this load was triggered by a jump, flash the target row.
        if (pendingFlash.current != null) {
          const idx = pendingFlash.current;
          pendingFlash.current = null;
          requestAnimationFrame(() => flashAndScroll(idx));
        }
      })
      .catch((e) => {
        if (token === reqToken.current) setError(e.message);
      })
      .finally(() => {
        if (token === reqToken.current) setLoading(false);
      });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [showingResult, activeRun?.id, page, affectedOnly]);

  useEffect(() => () => clearTimeout(flashTimer.current), []);

  /* ---- lazy-load more of the original file as the sentinel scrolls in ---- */
  useEffect(() => {
    if (!browsingOriginal || previewDone) return;
    const root = scrollRef.current;
    const el = sentinelRef.current;
    if (!root || !el) return;
    const io = new IntersectionObserver(
      (entries) => {
        if (entries.some((e) => e.isIntersecting)) loadMorePreview();
      },
      { root, rootMargin: "300px" }
    );
    io.observe(el);
    return () => io.disconnect();
  }, [browsingOriginal, previewDone, loadMorePreview]);

  /* ---- keep filling until the pane actually scrolls (short files / tall
         viewports never trigger the sentinel on their own) ---- */
  useEffect(() => {
    if (!browsingOriginal || previewDone || previewLoading) return;
    const root = scrollRef.current;
    if (root && root.scrollHeight <= root.clientHeight) loadMorePreview();
  }, [browsingOriginal, previewDone, previewLoading, previewRows, loadMorePreview]);

  /* ---- report original-file meta to the status bar as rows accumulate ---- */
  useEffect(() => {
    if (showingResult) return;
    onMeta?.({
      preview: true,
      total: previewRows.length,
      shown: previewRows.length,
      eof: previewDone,
    });
  }, [showingResult, previewRows.length, previewDone, onMeta]);

  const goToRow = useCallback(
    (n) => {
      if (!Number.isFinite(n) || total === 0) return;
      const target = Math.max(1, Math.min(total, Math.floor(n)));
      const p = Math.floor((target - 1) / PAGE_SIZE) + 1;
      const localIdx = (target - 1) % PAGE_SIZE;
      if (p === page) {
        flashAndScroll(localIdx);
      } else {
        pendingFlash.current = localIdx;
        setPage(p);
      }
    },
    [total, page, flashAndScroll]
  );

  function submitJump(e) {
    e.preventDefault();
    const n = parseInt(jumpText, 10);
    if (Number.isFinite(n)) goToRow(n);
  }

  function toggleAffected() {
    if (!hasFlag) return;
    pendingFlash.current = null;
    setPage(1);
    setAffectedOnly((v) => !v);
  }

  const toggleRow = useCallback(
    (i) => setOpenRow((prev) => (prev === i ? null : i)),
    []
  );

  // Decide grid contents: result rows, or the dataset preview.
  const gridColumns = showingResult && columns ? columns : dataset?.columns ?? [];
  const gridRows = showingResult ? rows : previewRows;

  // One cosmetic type map, shared by the grid headers and the detail panel.
  const types = useMemo(() => {
    const m = {};
    for (const c of gridColumns) m[c] = inferType(c, gridRows.map((r) => r?.[c]));
    return m;
  }, [gridColumns, gridRows]);

  const openRowData = openRow != null ? gridRows[openRow] : null;

  const overlay = renderOverlay(activeRun, error, onCancel);

  const shownStart = total === 0 ? 0 : rowOffset + 1;
  const shownEnd = rowOffset + rows.length;

  return (
    <div className="grid-pane">
      <div className="grid-main">
        <div className="grid-scroll" ref={scrollRef}>
          <DataGrid
            columns={gridColumns}
            rows={gridRows}
            types={types}
            targets={targets}
            onToggleTarget={onToggleTarget}
            rowOffset={rowOffset}
            selectedCell={selectedCell}
            onSelectCell={onSelectCell}
            flashIndex={showingResult ? flashIndex : null}
            showMatches={showingResult && hasFlag}
            openRow={openRow}
            onOpenRow={toggleRow}
          />

          {browsingOriginal && !previewDone && (
            <div className="grid-sentinel" ref={sentinelRef}>
              {previewLoading && (
                <>
                  <span className="spinner" /> Loading more rows…
                </>
              )}
            </div>
          )}
          {browsingOriginal && previewError && (
            <div className="grid-sentinel err">{previewError}</div>
          )}
          {browsingOriginal && previewDone && !previewError && previewRows.length > 0 && (
            <div className="grid-end">
              End of file · {fmtInt(previewRows.length)} rows
            </div>
          )}

          {showingResult && loading && (
            <div className="grid-overlay">
              <div className="state-card">
                <div className="spinner lg" style={{ margin: "0 auto 16px" }} />
                <h3>Loading rows…</h3>
                <p>Fetching this page from your processed file.</p>
              </div>
            </div>
          )}
          {overlay}
        </div>

        {openRowData && (
          <RowDetail
            rowNumber={openRowData.__rownum ?? rowOffset + openRow + 1}
            row={openRowData}
            columns={gridColumns}
            types={types}
            targets={targets}
            matched={showingResult && hasFlag && !!openRowData.__matched__}
            onClose={() => setOpenRow(null)}
          />
        )}
      </div>

      {showingResult && !error && (
        <div className="grid-toolbar">
          <div className="gt-left">
            <button
              type="button"
              className={`affect-toggle ${affectedOnly ? "on" : ""}`}
              onClick={toggleAffected}
              disabled={!hasFlag || matchedTotal === 0}
              title={
                !hasFlag
                  ? "Affected rows aren’t available for this result"
                  : matchedTotal === 0
                  ? "No rows were affected"
                  : affectedOnly
                  ? "Show all rows"
                  : "Show only affected rows"
              }
            >
              <span className="affect-dot" />
              {affectedOnly ? "Affected only" : "Show affected only"}
              <span className="affect-count">{fmtInt(matchedTotal)}</span>
            </button>
          </div>

          <div className="gt-pager">
            <button
              type="button"
              className="pg-btn"
              onClick={() => setPage((p) => Math.max(1, p - 1))}
              disabled={page <= 1 || loading}
              aria-label="Previous page"
            >
              ‹
            </button>
            <span className="pg-range">
              {total === 0 ? (
                "No rows"
              ) : (
                <>
                  <b>{fmtInt(shownStart)}</b>–<b>{fmtInt(shownEnd)}</b>
                  <span className="pg-of"> of {fmtInt(total)}</span>
                </>
              )}
            </span>
            <button
              type="button"
              className="pg-btn"
              onClick={() => setPage((p) => Math.min(numPages, p + 1))}
              disabled={page >= numPages || loading}
              aria-label="Next page"
            >
              ›
            </button>

            <form className="pg-jump" onSubmit={submitJump}>
              <label>Go to row</label>
              <input
                type="number"
                min="1"
                max={total || 1}
                value={jumpText}
                onChange={(e) => setJumpText(e.target.value)}
                placeholder="#"
                disabled={total === 0}
              />
              <button type="submit" className="pg-go" disabled={total === 0}>
                Go
              </button>
            </form>
          </div>

          <div className="gt-right">
            <ExportMenu
              jobId={activeRun.id}
              affectedOnly={affectedOnly}
              totalRows={totalAll}
              matchedRows={matchedTotal}
              columnCount={columns?.length ?? 0}
            />
          </div>
        </div>
      )}
    </div>
  );
}

function renderOverlay(run, error, onCancel) {
  if (!run) return null;

  if (error) {
    return (
      <div className="grid-overlay">
        <div className="state-card">
          <div className="ico fail">!</div>
          <h3>Couldn’t load the results</h3>
          <p>{error}</p>
        </div>
      </div>
    );
  }

  if (!TERMINAL.has(run.status)) {
    return (
      <div className="grid-overlay">
        <div className="state-card">
          <div className="ico run">
            <div className="spinner lg" />
          </div>
          <h3>Working through your data</h3>
          <p>
            Applying your pattern across every row — the results appear here as
            soon as they’re ready.
          </p>
          <div className="state-progress">
            <i style={{ width: `${run.progress || 4}%` }} />
          </div>
          <div className="state-pct">
            {prettyStage(run.stage)} · {run.progress || 0}%
          </div>
          {onCancel && (
            <button
              className="btn danger tiny"
              style={{ marginTop: 18 }}
              onClick={() => onCancel(run)}
            >
              Cancel
            </button>
          )}
        </div>
      </div>
    );
  }

  if (run.status === "FAILED") {
    return (
      <div className="grid-overlay">
        <div className="state-card">
          <div className="ico fail">✕</div>
          <h3>This didn’t work</h3>
          <p>{run.error_message || "The change couldn’t be completed."}</p>
        </div>
      </div>
    );
  }

  if (run.status === "CANCELLED") {
    return (
      <div className="grid-overlay">
        <div className="state-card">
          <div className="ico cancel">⊘</div>
          <h3>Cancelled</h3>
          <p>This change was cancelled before it finished.</p>
        </div>
      </div>
    );
  }

  return null;
}
