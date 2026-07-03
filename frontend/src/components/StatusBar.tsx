import { fmtInt } from "../lib/format";
import ThemeToggle from "./ThemeToggle";
import type { GridMeta, Job, Theme } from "../lib/api-types";

interface Props {
  activeRun: Job | null;
  targets: string[];
  gridMeta: GridMeta | null;
  cellRef: string;
  theme: Theme;
  onToggleTheme: () => void;
}

/** Bottom status bar: counts, selection, and where you are in the result. */
export default function StatusBar({
  activeRun,
  targets,
  gridMeta,
  cellRef,
  theme,
  onToggleTheme,
}: Props) {
  const isResult = activeRun && activeRun.status === "SUCCESS";
  const preview = gridMeta?.preview;
  const eof = gridMeta?.eof;
  const total = gridMeta?.total ?? 0;
  const totalAll = gridMeta?.totalAll ?? total;
  const matchedTotal = gridMeta?.matchedTotal ?? 0;
  const affectedOnly = gridMeta?.affectedOnly;
  const page = gridMeta?.page ?? 1;
  const numPages = gridMeta?.numPages ?? 1;

  return (
    <div className="statusbar">
      <span className="sb-item accent">
        {isResult ? <b>Result</b> : <b>Original</b>}
        {affectedOnly && <span className="sb-pill">affected only</span>}
      </span>

      <span className="sb-item">
        {preview ? (
          <>
            <b className="mono">{fmtInt(total)}</b> {eof ? "rows" : "rows loaded"}
          </>
        ) : isResult ? (
          affectedOnly ? (
            <>
              <b className="mono">{fmtInt(total)}</b> affected rows
            </>
          ) : (
            <>
              <b className="mono">{fmtInt(totalAll)}</b> rows
            </>
          )
        ) : (
          <>
            <b className="mono">{fmtInt(total)}</b> rows
          </>
        )}
      </span>

      {isResult && (
        <span className="sb-item">
          <b className="mono">{fmtInt(matchedTotal || activeRun?.matched_rows)}</b>{" "}
          affected
        </span>
      )}

      <span className="sb-item">
        <b className="mono">{targets.length}</b>{" "}
        {targets.length === 1 ? "column" : "columns"} selected
      </span>

      {cellRef && (
        <span className="sb-item mono">
          <span aria-hidden="true">▣</span> {cellRef}
        </span>
      )}

      <span className="sb-spacer" />

      {isResult && numPages > 1 && (
        <span className="sb-item tail">
          Page <b className="mono">{fmtInt(page)}</b>
          <span style={{ color: "var(--faint)" }}>/ {fmtInt(numPages)}</span>
        </span>
      )}

      <ThemeToggle theme={theme} onToggle={onToggleTheme} className="sb-toggle" />
    </div>
  );
}
