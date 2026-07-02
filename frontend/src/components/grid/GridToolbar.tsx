import type { Dispatch, FormEvent, SetStateAction } from "react";
import { fmtInt } from "../../lib/format";
import ExportMenu from "./ExportMenu";
import type { Job } from "../../lib/api-types";

interface Props {
  activeRun: Job;
  hasFlag: boolean;
  matchedTotal: number;
  affectedOnly: boolean;
  onToggleAffected: () => void;
  page: number;
  numPages: number;
  loading: boolean;
  setPage: Dispatch<SetStateAction<number>>;
  total: number;
  totalAll: number;
  shownStart: number;
  shownEnd: number;
  jumpText: string;
  setJumpText: Dispatch<SetStateAction<string>>;
  onSubmitJump: (e: FormEvent) => void;
  columns: string[] | null;
}

/**
 * The grid's bottom toolbar: affected-only toggle, pager + jump-to-row form,
 * and the export menu. Only rendered while viewing a successful run's result.
 */
export default function GridToolbar({
  activeRun,
  hasFlag,
  matchedTotal,
  affectedOnly,
  onToggleAffected,
  page,
  numPages,
  loading,
  setPage,
  total,
  totalAll,
  shownStart,
  shownEnd,
  jumpText,
  setJumpText,
  onSubmitJump,
  columns,
}: Props) {
  return (
    <div className="grid-toolbar">
      <div className="gt-left">
        <button
          type="button"
          className={`affect-toggle ${affectedOnly ? "on" : ""}`}
          onClick={onToggleAffected}
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

        <form className="pg-jump" onSubmit={onSubmitJump}>
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
  );
}
