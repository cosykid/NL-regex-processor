import { useRef, useState } from "react";
import { uploadFile } from "../api/client";
import { errorMessage } from "../lib/errors";
import type { Dataset } from "../lib/api-types";

interface Props {
  onDataset: (dataset: Dataset) => void;
}

/** Empty-state importer — the front door when no file is open. */
export default function ImportView({ onDataset }: Props) {
  const [busy, setBusy] = useState(false);
  const [drag, setDrag] = useState(false);
  const [error, setError] = useState("");
  const inputRef = useRef<HTMLInputElement>(null);

  async function send(file?: File) {
    if (!file) return;
    setError("");
    setBusy(true);
    try {
      onDataset(await uploadFile(file));
    } catch (e) {
      setError(errorMessage(e));
      setBusy(false);
    }
  }

  return (
    <div className="import-wrap">
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
          onDragOver={(e) => {
            e.preventDefault();
            setDrag(true);
          }}
          onDragLeave={() => setDrag(false)}
          onDrop={(e) => {
            e.preventDefault();
            setDrag(false);
            if (!busy) send(e.dataTransfer.files[0]);
          }}
          onClick={() => !busy && inputRef.current?.click()}
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
              "Opening your file…"
            ) : (
              <>
                Drop a file or <b>browse</b>
              </>
            )}
          </div>
          <div className="dz-sub">
            CSV or Excel — only the header is read to get you started
          </div>
        </div>

        {error && <div className="alert error">{error}</div>}
      </div>
    </div>
  );
}
