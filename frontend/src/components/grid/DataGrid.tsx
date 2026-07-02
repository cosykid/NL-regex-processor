import { useMemo } from "react";
import { inferType } from "../../lib/types";
import type { ColumnType, Row } from "../../lib/api-types";

/**
 * The spreadsheet grid — the centerpiece of the workspace.
 * Sticky typed headers + a sticky row-number gutter; click a header to
 * select/deselect it as a transformation target (Sheets-style), click a cell
 * to select it. Purely presentational — all state is lifted to the workspace.
 */
const EXPAND_ICON = (
  <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
    <path d="M15 3h6v6M9 21H3v-6M21 3l-7 7M3 21l7-7" />
  </svg>
);

interface Props {
  columns?: string[];
  rows?: Row[];
  types?: Record<string, ColumnType>;
  targets?: string[];
  onToggleTarget?: (col: string) => void;
  rowOffset?: number;
  selectedCell?: string | null;
  onSelectCell?: (key: string, label: string) => void;
  flashIndex?: number | null;
  showMatches?: boolean;
  openRow?: number | null;
  onOpenRow?: (i: number) => void;
}

export default function DataGrid({
  columns = [],
  rows = [],
  types: typesProp,
  targets = [],
  onToggleTarget,
  rowOffset = 0,
  selectedCell,
  onSelectCell,
  flashIndex = null,
  showMatches = false,
  openRow = null,
  onOpenRow,
}: Props) {
  const targetSet = new Set(targets);

  // Infer a cosmetic type per column from the visible rows (unless the parent
  // already computed the shared map).
  const computed = useMemo(() => {
    const m: Record<string, ColumnType> = {};
    for (const c of columns) m[c] = inferType(c, rows.map((r) => r?.[c]));
    return m;
  }, [columns, rows]);
  const types = typesProp || computed;

  return (
    <table className="grid">
      <thead>
        <tr>
          <th className="gutter" />
          {columns.map((c) => {
            const on = targetSet.has(c);
            const t = types[c];
            return (
              <th key={c} className={on ? "target" : ""}>
                <div
                  className="colhead"
                  onClick={() => onToggleTarget && onToggleTarget(c)}
                  title={
                    onToggleTarget
                      ? on
                        ? `“${c}” is a target — click to remove`
                        : `Click to target “${c}”`
                      : c
                  }
                >
                  <span className="col-type" title={t.title}>
                    {t.glyph}
                  </span>
                  <span className="col-name">{c}</span>
                  {onToggleTarget && (
                    <span className="col-check">{on ? "✓" : ""}</span>
                  )}
                </div>
              </th>
            );
          })}
        </tr>
      </thead>
      <tbody>
        {rows.map((row, i) => {
          const matched = showMatches && row?.__matched__;
          const cls = [
            matched ? "matched" : "",
            flashIndex === i ? "flash" : "",
            openRow === i ? "open" : "",
          ]
            .filter(Boolean)
            .join(" ");
          // Prefer the backend's original row number (preserved when the
          // affected-only filter is on); fall back to positional numbering.
          const rowNo = row?.__rownum ?? rowOffset + i + 1;
          return (
          <tr key={i} data-ri={i} className={cls}>
            <td
              className="gutter"
              onClick={() => onOpenRow && onOpenRow(i)}
              title={onOpenRow ? "Expand row" : undefined}
            >
              {onOpenRow && (
                <span className="row-expand" aria-hidden="true">
                  {EXPAND_ICON}
                </span>
              )}
              <span className="rn">{rowNo}</span>
            </td>
            {columns.map((c) => {
              const isSel = selectedCell === `${i}:${c}`;
              const raw = row?.[c];
              const val = raw === null || raw === undefined ? "" : String(raw);
              return (
                <td
                  key={c}
                  className={`${targetSet.has(c) ? "target" : ""} ${
                    isSel ? "sel" : ""
                  }`}
                  onClick={() =>
                    onSelectCell && onSelectCell(`${i}:${c}`, `R${rowNo} · ${c}`)
                  }
                >
                  <span className={`cell ${val ? "" : "empty"}`} title={val}>
                    {val || "∅"}
                  </span>
                </td>
              );
            })}
          </tr>
          );
        })}
      </tbody>
    </table>
  );
}
