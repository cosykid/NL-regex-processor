import { Fragment, useEffect, useRef } from "react";
import type { KeyboardEvent } from "react";
import { TERMINAL, VALUE_ACTIONS } from "../lib/constants";
import ActionMenu from "./ActionMenu";
import type { Job, JobAction, ResolvedAction } from "../lib/api-types";

const EXAMPLES = [
  "Email addresses",
  "Phone numbers",
  "Dates like 2024-01-31",
  "Name starts with A and phone starts with 0",
];

const ACTION_LABEL: Record<Exclude<ResolvedAction, "">, string> = {
  find: "Find only",
  replace: "Replace",
  mask: "Mask",
  extract: "Extract",
  keep: "Keep rows",
  drop: "Drop rows",
};

// What the prompt should describe depends on the chosen action: under `auto`
// it carries the action (and any value) too; everywhere else the action is
// already fixed, so the prompt only scopes the match.
const PROMPT_PLACEHOLDER: Record<JobAction, string> = {
  auto: "Describe what to find and what to do — e.g. replace emails with REDACTED, or drop rows where phone starts with 0",
  find: "Describe what to find — e.g. email addresses, or name starts with A",
  replace: "Describe what to replace — e.g. email addresses, or dates like 2024-01-31",
  mask: "Describe what to mask — e.g. card numbers, or email addresses",
  extract: "Describe what to keep from each cell — e.g. the email address",
  keep: "Describe rows to keep — e.g. name starts with A and phone starts with 0",
  drop: "Describe rows to drop — e.g. name starts with A and phone starts with 0",
};

interface Props {
  prompt: string;
  setPrompt: (value: string) => void;
  replacement: string;
  setReplacement: (value: string) => void;
  action: JobAction;
  setAction: (value: JobAction) => void;
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
  action,
  setAction,
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
  const showValue = VALUE_ACTIONS.has(action);
  // The box only shows for replace/mask (VALUE_ACTIONS), so blank has exactly
  // one meaning per action; the placeholder carries the short version and the
  // tooltip the full one.
  const valueHint =
    action === "mask"
      ? {
          placeholder: "mask text — blank uses ••••",
          title:
            "Text written over each match. Leave blank to use the default •••• token.",
        }
      : {
          placeholder: "new text — blank removes match",
          title:
            "Text each match is replaced with. Leave blank to delete the matched text.",
        };

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
              placeholder={PROMPT_PLACEHOLDER[action]}
              spellCheck={false}
              autoFocus
            />
          </div>
          <div className="c-divider" />
          <div className={"c-replace" + (showValue ? " has-value" : "")}>
            <span className="arrow">→</span>
            <ActionMenu value={action} onChange={setAction} />
            {showValue && (
              <input
                value={replacement}
                onChange={(e) => setReplacement(e.target.value)}
                onKeyDown={onKey}
                placeholder={valueHint.placeholder}
                title={valueHint.title}
                spellCheck={false}
                aria-label={action === "mask" ? "Mask text" : "Replacement text"}
              />
            )}
          </div>
        </div>
        <button
          className="run-btn"
          onClick={onRun}
          disabled={!canRun}
          title={
            running
              ? "A run is already in progress"
              : !prompt.trim()
              ? "Describe what to find first"
              : targets.length === 0
              ? "Choose one or more columns in the grid first"
              : "Run (Enter)"
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
            <button
              className="ex"
              key={ex}
              onClick={() => {
                setPrompt(ex);
                // hand focus back so the prompt can be tweaked or run at once
                inputRef.current?.focus();
              }}
            >
              {ex}
            </button>
          ))}
        </div>
      </div>

      {error && (
        <div className="composer-aux" style={{ paddingTop: 0, paddingBottom: 12 }}>
          <span className="alert error" role="alert" style={{ margin: 0, width: "100%" }}>
            {error}
          </span>
        </div>
      )}

      {activeRun && (activeRun.predicates?.length || activeRun.regex_pattern) && (
        <div className="pattern-strip">
          {activeRun.resolved_action && (
            <span className="action-badge" title="Output action">
              {activeRun.action === "auto" && (
                <span className="ab-auto">auto →</span>
              )}
              {ACTION_LABEL[activeRun.resolved_action]}
            </span>
          )}
          {activeRun.predicates?.length > 1 ? (
            <>
              <span className="tag">Conditions</span>
              <span className="predicates">
                {activeRun.predicates.map((p, i) => (
                  <Fragment key={`${p.column}-${i}`}>
                    {i > 0 && (
                      <span className="conj">
                        {activeRun.combinator === "all" ? "AND" : "OR"}
                      </span>
                    )}
                    <span className="pred" title={p.explanation}>
                      <span className="pcol">{p.column}</span>
                      <code>{p.pattern}</code>
                    </span>
                  </Fragment>
                ))}
              </span>
            </>
          ) : (
            <>
              <span className="tag">Pattern</span>
              <code
                title={
                  activeRun.predicates?.[0]?.pattern ?? activeRun.regex_pattern
                }
              >
                {activeRun.predicates?.[0]?.pattern ?? activeRun.regex_pattern}
              </code>
            </>
          )}
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
