import { TERMINAL } from "../../lib/constants";
import { prettyStage } from "../../lib/stage";
import type { Job } from "../../lib/api-types";

interface Props {
  run: Job | null;
  error: string;
  onCancel?: (run: Job) => void;
}

/**
 * State overlay drawn over the grid for a run that isn't a clean success:
 * in-progress (with a cancel button), failed, cancelled, or a load error for
 * an otherwise-successful run's results.
 */
export default function RunOverlay({ run, error, onCancel }: Props) {
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
