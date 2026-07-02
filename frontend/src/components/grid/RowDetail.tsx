import { useEffect, useState } from "react";
import type { ColumnType, Row } from "../../lib/api-types";

interface Props {
  rowNumber: number;
  row: Row;
  columns?: string[];
  types?: Record<string, ColumnType>;
  targets?: string[];
  matched?: boolean;
  onClose?: () => void;
}

/**
 * Right-hand inspector for a single row (Neon-style): every column laid out as
 * a labelled field with its inferred type and full, wrapping value — so a row
 * whose cells are clipped in the grid can be read (and copied) in full. Opened
 * from the row's gutter, closed with the button or Escape.
 */
export default function RowDetail({
  rowNumber,
  row,
  columns = [],
  types = {},
  targets = [],
  matched = false,
  onClose,
}: Props) {
  const targetSet = new Set(targets);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose?.();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);

  return (
    <aside className="row-detail" aria-label={`Row ${rowNumber} details`}>
      <div className="rd-head">
        <div className="rd-title">
          <span className="rd-eyebrow">Row</span>
          <span className="rd-num">{rowNumber}</span>
          {matched && <span className="rd-badge">affected</span>}
        </div>
        <button
          className="rd-close"
          onClick={onClose}
          aria-label="Close details"
          title="Close (Esc)"
        >
          ✕
        </button>
      </div>

      <div className="rd-fields">
        {columns.map((c) => {
          const raw = row?.[c];
          const val = raw === null || raw === undefined ? "" : String(raw);
          return (
            <Field
              key={c}
              name={c}
              type={types[c]}
              value={val}
              isTarget={targetSet.has(c)}
            />
          );
        })}
      </div>

      <div className="rd-foot">
        <button className="btn block" onClick={onClose}>
          Close
        </button>
      </div>
    </aside>
  );
}

interface FieldProps {
  name: string;
  type?: ColumnType;
  value: string;
  isTarget: boolean;
}

function Field({ name, type, value, isTarget }: FieldProps) {
  const [copied, setCopied] = useState(false);
  const empty = value === "";

  async function copy() {
    try {
      await navigator.clipboard.writeText(value);
      setCopied(true);
      setTimeout(() => setCopied(false), 1200);
    } catch {
      /* clipboard unavailable — no-op */
    }
  }

  return (
    <div className={`rd-field ${isTarget ? "target" : ""}`}>
      <div className="rd-label">
        <span className="rd-name">
          {isTarget && <span className="rd-tdot" aria-hidden="true" />}
          {name}
        </span>
        {type && <span className="rd-type">{type.title}</span>}
      </div>
      <div className={`rd-value ${empty ? "empty" : ""}`}>
        <span className="rd-val-text">{empty ? "∅ empty" : value}</span>
        {!empty && (
          <button
            className="rd-copy"
            onClick={copy}
            title="Copy value"
            aria-label="Copy value"
          >
            {copied ? (
              <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.4" strokeLinecap="round" strokeLinejoin="round">
                <path d="M5 13l4 4L19 7" />
              </svg>
            ) : (
              <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                <rect x="9" y="9" width="11" height="11" rx="2" />
                <path d="M5 15V5a2 2 0 0 1 2-2h10" />
              </svg>
            )}
          </button>
        )}
      </div>
    </div>
  );
}
