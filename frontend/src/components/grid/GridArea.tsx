import { useMemo, useRef } from "react";
import { fmtInt } from "../../lib/format";
import { inferType } from "../../lib/types";
import { usePreviewRows } from "../../hooks/usePreviewRows";
import { useResultPage, PAGE_SIZE } from "../../hooks/useResultPage";
import DataGrid from "./DataGrid";
import RowDetail from "./RowDetail";
import GridToolbar from "./GridToolbar";
import RunOverlay from "./RunOverlay";
import type { ColumnType, Dataset, GridMeta, Job } from "../../lib/api-types";

export { PAGE_SIZE };

interface Props {
  dataset: Dataset;
  activeRun: Job | null;
  targets: string[];
  onToggleTarget: (col: string) => void;
  onMeta: (meta: GridMeta) => void;
  selectedCell: string | null;
  onSelectCell: (key: string, label: string) => void;
  onCancel: (run: Job) => void;
}

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
}: Props) {
  const showingResult = !!activeRun && activeRun.status === "SUCCESS";

  const scrollRef = useRef<HTMLDivElement>(null);
  const sentinelRef = useRef<HTMLDivElement>(null); // bottom marker that drives lazy loading

  // Only the pure "no run selected" state browses the original file; a running
  // or failed run shows the preview as a backdrop behind an overlay, so we must
  // not fetch more there.
  const browsingOriginal = !activeRun;

  const {
    previewRows,
    previewDone,
    previewLoading,
    previewError,
  } = usePreviewRows({ dataset, browsingOriginal, showingResult, scrollRef, sentinelRef, onMeta });

  const {
    columns,
    rows,
    total,
    totalAll,
    matchedTotal,
    hasFlag,
    affectedOnly,
    page,
    setPage,
    loading,
    error,
    jumpText,
    setJumpText,
    flashIndex,
    openRow,
    numPages,
    rowOffset,
    submitJump,
    toggleAffected,
    toggleRow,
    setOpenRow,
  } = useResultPage({ activeRun, showingResult, scrollRef, onMeta });

  // Decide grid contents: result rows, or the dataset preview.
  const gridColumns = showingResult && columns ? columns : dataset?.columns ?? [];
  const gridRows = showingResult ? rows : previewRows;

  // One cosmetic type map, shared by the grid headers and the detail panel.
  const types = useMemo(() => {
    const m: Record<string, ColumnType> = {};
    for (const c of gridColumns) m[c] = inferType(c, gridRows.map((r) => r?.[c]));
    return m;
  }, [gridColumns, gridRows]);

  const openRowData = openRow != null ? gridRows[openRow] : null;

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
          <RunOverlay run={activeRun} error={error} onCancel={onCancel} />
        </div>

        {openRowData && openRow != null && (
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

      {showingResult && !error && activeRun && (
        <GridToolbar
          activeRun={activeRun}
          hasFlag={hasFlag}
          matchedTotal={matchedTotal}
          affectedOnly={affectedOnly}
          onToggleAffected={toggleAffected}
          page={page}
          numPages={numPages}
          loading={loading}
          setPage={setPage}
          total={total}
          totalAll={totalAll}
          shownStart={shownStart}
          shownEnd={shownEnd}
          jumpText={jumpText}
          setJumpText={setJumpText}
          onSubmitJump={submitJump}
          columns={columns}
        />
      )}
    </div>
  );
}
