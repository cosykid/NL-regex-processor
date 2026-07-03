import { useRef, useState } from "react";
import { uploadFile } from "../api/client";
import { errorMessage } from "../lib/errors";
import { fmtBytes, timeAgo } from "../lib/format";
import { getRecents, removeRecent } from "../lib/recents";
import type { Dataset } from "../lib/api-types";

interface Props {
  onDataset: (dataset: Dataset) => void;
  onReopen: (id: string) => Promise<void>;
}

/** Empty-state importer — the front door when no file is open. */
export default function ImportView({ onDataset, onReopen }: Props) {
  const [busy, setBusy] = useState(false);
  const [progress, setProgress] = useState(0);
  const [drag, setDrag] = useState(false);
  const [error, setError] = useState("");
  const [recents, setRecents] = useState(getRecents);
  const [reopening, setReopening] = useState<string | null>(null);
  const inputRef = useRef<HTMLInputElement>(null);

  // Mirrors the picker's `accept` list — a drop bypasses that filter, so guard
  // here too instead of letting the upload fail server-side.
  const SUPPORTED = /\.(csv|xlsx|xls|xlsm)$/i;

  async function send(file?: File) {
    if (!file) return;
    if (!SUPPORTED.test(file.name)) {
      setError(
        `“${file.name}” isn’t a supported file — use CSV or Excel (.xlsx, .xls, .xlsm).`
      );
      return;
    }
    setError("");
    setProgress(0);
    setBusy(true);
    try {
      onDataset(await uploadFile(file, setProgress));
    } catch (e) {
      setError(errorMessage(e));
      setBusy(false);
    }
  }

  async function reopen(id: string) {
    if (busy || reopening) return;
    setError("");
    setReopening(id);
    try {
      // On success the workspace mounts and this view unmounts.
      await onReopen(id);
    } catch (e) {
      setError(errorMessage(e));
      setRecents(getRecents()); // a gone file was dropped from the list
      setReopening(null);
    }
  }

  function forget(id: string) {
    removeRecent(id);
    setRecents(getRecents());
  }

  const pct = Math.round(progress * 100);

  return (
    <div
      className="import-wrap"
      onDragOver={(e) => {
        e.preventDefault();
        setDrag(true);
      }}
      onDragLeave={(e) => {
        // ignore leaves that just cross into a child element
        if (e.currentTarget.contains(e.relatedTarget as Node)) return;
        setDrag(false);
      }}
      onDrop={(e) => {
        e.preventDefault();
        setDrag(false);
        if (!busy) send(e.dataTransfer.files[0]);
      }}
    >
      <div className="import-card">
        <div className="import-kicker">Plain language · every match · at scale</div>
        <h1>
          Describe what to change.
          <br />
          <em>Reshape</em> a million rows.
        </h1>
        <p className="lede">
          Open a spreadsheet, say what you want to find in plain language, and
          replace it everywhere — across the whole file, as many passes as you
          like.
        </p>

        <div
          className={`dropzone ${drag ? "drag" : ""}`}
          role="button"
          tabIndex={0}
          aria-label="Upload a CSV or Excel file"
          aria-busy={busy}
          onClick={() => !busy && inputRef.current?.click()}
          onKeyDown={(e) => {
            if (busy) return;
            if (e.key === "Enter" || e.key === " ") {
              e.preventDefault();
              inputRef.current?.click();
            }
          }}
        >
          <input
            ref={inputRef}
            type="file"
            accept=".csv,.xlsx,.xls,.xlsm"
            hidden
            onChange={(e) => send(e.target.files?.[0])}
          />
          <div className="dz-icon">
            {busy ? (
              <span className="spinner lg" />
            ) : (
              <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8">
                <path
                  d="M12 16V4m0 0L7 9m5-5 5 5"
                  strokeLinecap="round"
                  strokeLinejoin="round"
                />
                <path
                  d="M4 15v3a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2v-3"
                  strokeLinecap="round"
                />
              </svg>
            )}
          </div>
          <div className="dz-title">
            {busy ? (
              progress < 0.95 ? (
                `Uploading… ${pct}%`
              ) : (
                "Finishing up…"
              )
            ) : (
              <>
                Drop a file or <b>browse</b>
              </>
            )}
          </div>
          {busy ? (
            <div
              className="import-progress"
              role="progressbar"
              aria-valuenow={pct}
              aria-valuemin={0}
              aria-valuemax={100}
            >
              <i style={{ width: `${Math.max(pct, 4)}%` }} />
            </div>
          ) : (
            <div className="dz-sub">
              CSV or Excel — only the header is read to get you started
            </div>
          )}
        </div>

        {error && <div className="alert error">{error}</div>}

        {recents.length > 0 && (
          <div className="recents">
            <div className="recents-head">Recent files</div>
            <ul className="recents-list">
              {recents.map((r) => (
                <li key={r.id} className="recent">
                  <button
                    className="recent-open"
                    onClick={() => reopen(r.id)}
                    disabled={busy || !!reopening}
                    title={`Reopen ${r.original_name}`}
                  >
                    <span className="recent-icon" aria-hidden="true">
                      {reopening === r.id ? <span className="spinner" /> : "▦"}
                    </span>
                    <span className="recent-body">
                      <span className="recent-name">{r.original_name}</span>
                      <span className="recent-meta">
                        <b>{r.columns}</b> cols · {(r.kind || "csv").toUpperCase()} ·{" "}
                        {fmtBytes(r.size_bytes)} · {timeAgo(r.opened_at)}
                      </span>
                    </span>
                  </button>
                  <button
                    className="recent-forget"
                    onClick={() => forget(r.id)}
                    disabled={!!reopening}
                    aria-label={`Forget ${r.original_name}`}
                    title="Remove from this list"
                  >
                    ✕
                  </button>
                </li>
              ))}
            </ul>
          </div>
        )}
      </div>
    </div>
  );
}
