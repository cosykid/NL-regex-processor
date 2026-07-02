import { fmtBytes, fmtInt, timeAgo, prettyStage, TERMINAL } from "../util";

/**
 * Left rail: the open file plus its full history of changes. Every pass the
 * file has been through is listed here; click one to view its result, or start
 * another from the composer — a file is never "used up".
 */
export default function Sidebar({
  dataset,
  runs,
  activeRunId,
  onSelectRun,
  onNewRun,
  onImportAnother,
}) {
  return (
    <aside className="sidebar">
      <div className="ds-card">
        <div className="eyebrow ds-eyebrow">File</div>
        <div className="ds-name">
          <span className="fi">▦</span>
          <span>{dataset.original_name}</span>
        </div>
        <div className="ds-meta">
          <span>
            <b>{dataset.columns.length}</b> columns
          </span>
          <span>
            <b>{(dataset.kind || "csv").toUpperCase()}</b>
          </span>
          <span>{fmtBytes(dataset.size_bytes)}</span>
        </div>
      </div>

      <div className="rail-head">
        <span className="ttl">History</span>
        <span className="count">{runs.length}</span>
      </div>

      <div className="runs">
        <button
          className={`run new ${activeRunId === null ? "active" : ""}`}
          onClick={onNewRun}
        >
          <div className="run-top">
            <span className="run-dot" style={{ background: "var(--accent)" }} />
            <span className="run-prompt">＋ New change</span>
          </div>
          <div className="run-sub">view the original data &amp; compose a change</div>
        </button>

        {runs.length === 0 ? (
          <div className="run-empty">
            <b>Nothing yet.</b>
            <br />
            Choose columns and describe what to find above, then run it.
          </div>
        ) : (
          runs.map((r, i) => {
            const n = runs.length - i;
            const st = r.status.toLowerCase();
            return (
              <button
                key={r.id}
                className={`run ${activeRunId === r.id ? "active" : ""}`}
                onClick={() => onSelectRun(r)}
              >
                <div className="run-top">
                  <span className={`run-dot ${st}`} />
                  <span className="run-prompt">
                    {r.nl_prompt || "(untitled)"}
                  </span>
                  <span className="run-idx">#{String(n).padStart(2, "0")}</span>
                </div>
                <div className="run-sub">
                  {r.status === "SUCCESS" ? (
                    <span className="matched">
                      {fmtInt(r.matched_rows)} matched
                    </span>
                  ) : (
                    <span className="state">
                      {TERMINAL.has(r.status)
                        ? r.status.toLowerCase()
                        : prettyStage(r.stage)}
                    </span>
                  )}
                  <span style={{ marginLeft: "auto" }}>{timeAgo(r.created_at)}</span>
                </div>
              </button>
            );
          })
        )}
      </div>

      <div className="sidebar-foot">
        <button className="btn block" onClick={onImportAnother}>
          ↥ Open another file
        </button>
      </div>
    </aside>
  );
}
