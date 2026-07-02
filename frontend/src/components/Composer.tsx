import { useEffect, useRef } from "react";
import type { KeyboardEvent } from "react";
import { TERMINAL } from "../lib/constants";
import type { Job } from "../lib/api-types";

const EXAMPLES = [
  "Email addresses",
  "Phone numbers",
  "Dates like 2024-01-31",
  "Web addresses",
  "16-digit card numbers",
];

interface Props {
  prompt: string;
  setPrompt: (value: string) => void;
  replacement: string;
  setReplacement: (value: string) => void;
  targets: string[];
  onToggleTarget: (col: string) => void;
  onRun: () => void;
  activeRun: Job | null;
  focusSignal: number;
  error: string;
}

/**
 * The composer — describe what to find in plain language and what to replace
 * it with. Always available, so a file can be transformed again and again:
 * tweak the description or targets and run it to produce another result.
 */
export default function Composer({
  prompt,
  setPrompt,
  replacement,
  setReplacement,
  targets,
  onToggleTarget,
  onRun,
  activeRun,
  focusSignal,
  error,
}: Props) {
  const inputRef = useRef<HTMLInputElement>(null);
  const running = activeRun && !TERMINAL.has(activeRun.status);
  const canRun = prompt.trim() && targets.length > 0 && !running;

  useEffect(() => {
    inputRef.current?.focus();
  }, [focusSignal]);

  function onKey(e: KeyboardEvent<HTMLInputElement>) {
    if (e.key === "Enter" && canRun) {
      e.preventDefault();
      onRun();
    }
  }

  return (
    <div className="composer">
      <div className="composer-row">
        <div className="c-lead" aria-hidden="true">
          <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
            <circle cx="11" cy="11" r="7" />
            <line x1="21" y1="21" x2="16.5" y2="16.5" strokeLinecap="round" />
          </svg>
        </div>
        <div className="c-fields">
          <div className="c-prompt">
            <input
              ref={inputRef}
              value={prompt}
              onChange={(e) => setPrompt(e.target.value)}
              onKeyDown={onKey}
              placeholder="Describe what to find — e.g. email addresses, phone numbers, dates"
              spellCheck={false}
              autoFocus
            />
          </div>
          <div className="c-divider" />
          <div className="c-replace">
            <span className="arrow">→</span>
            <label>replace&nbsp;with</label>
            <input
              value={replacement}
              onChange={(e) => setReplacement(e.target.value)}
              onKeyDown={onKey}
              placeholder="blank = remove"
              spellCheck={false}
            />
          </div>
        </div>
        <button
          className="run-btn"
          onClick={onRun}
          disabled={!canRun}
          title={
            targets.length === 0
              ? "Choose one or more columns in the grid first"
              : "Run"
          }
        >
          {running ? (
            <>
              <span className="spinner" /> Working…
            </>
          ) : (
            <>
              Run <span className="kbd">⏎</span>
            </>
          )}
        </button>
      </div>

      <div className="composer-aux">
        <span className="aux-label">on</span>
        {targets.length > 0 ? (
          <div className="targets-inline">
            {targets.map((c) => (
              <span className="tchip" key={c}>
                {c}
                <button
                  onClick={() => onToggleTarget(c)}
                  title={`Remove ${c}`}
                  aria-label={`Remove ${c}`}
                >
                  ×
                </button>
              </span>
            ))}
          </div>
        ) : (
          <span className="targets-hint">
            choose columns by clicking their headers in the grid
          </span>
        )}
        <span className="aux-spacer" />
        <div className="examples">
          {EXAMPLES.map((ex) => (
            <button className="ex" key={ex} onClick={() => setPrompt(ex)}>
              {ex}
            </button>
          ))}
        </div>
      </div>

      {error && (
        <div className="composer-aux" style={{ paddingTop: 0, paddingBottom: 12 }}>
          <span className="alert error" style={{ margin: 0, width: "100%" }}>
            {error}
          </span>
        </div>
      )}

      {activeRun?.regex_pattern && (
        <div className="pattern-strip">
          <span className="tag">Pattern</span>
          <code>{activeRun.regex_pattern}</code>
          {activeRun.regex_explanation && (
            <span className="expl">{activeRun.regex_explanation}</span>
          )}
        </div>
      )}

      <div className="run-progress">
        {running && <i style={{ width: `${activeRun?.progress || 4}%` }} />}
      </div>
    </div>
  );
}
