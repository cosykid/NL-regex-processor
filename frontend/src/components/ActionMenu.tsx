import { useEffect, useRef, useState } from "react";
import type { KeyboardEvent } from "react";
import type { JobAction } from "../lib/api-types";

interface Option {
  value: JobAction;
  label: string;
  desc: string;
}

// The output actions, each with a one-line description shown in the menu so the
// choice is self-explanatory without leaving the composer.
const OPTIONS: Option[] = [
  { value: "auto", label: "Auto", desc: "AI picks the action — and any value — from your prompt" },
  { value: "find", label: "Find only", desc: "Count and highlight matches — change nothing" },
  { value: "replace", label: "Replace", desc: "Swap the matched text — leave blank to remove it" },
  { value: "mask", label: "Mask", desc: "Redact the match with •••• (or your own token)" },
  { value: "extract", label: "Extract", desc: "Keep only the match, drop the rest of the cell" },
  { value: "keep", label: "Keep rows", desc: "Keep only the rows that match, drop the rest" },
  { value: "drop", label: "Drop rows", desc: "Remove the rows that match, keep the rest" },
];

interface Props {
  value: JobAction;
  onChange: (value: JobAction) => void;
}

/**
 * The output-action picker. A native <select> can't style its option list or
 * carry per-option descriptions, so this is a custom listbox: a trigger button
 * plus a popover of labelled, described options with keyboard + click-outside
 * handling. Styled to the console tokens rather than the OS menu.
 */
export default function ActionMenu({ value, onChange }: Props) {
  const [open, setOpen] = useState(false);
  const [active, setActive] = useState(0); // highlighted row for keyboard nav
  const rootRef = useRef<HTMLDivElement>(null);
  const btnRef = useRef<HTMLButtonElement>(null);

  const current = OPTIONS.find((o) => o.value === value) ?? OPTIONS[0];

  // Close on an outside click while open.
  useEffect(() => {
    if (!open) return;
    function onDoc(e: MouseEvent) {
      if (!rootRef.current?.contains(e.target as Node)) setOpen(false);
    }
    document.addEventListener("mousedown", onDoc);
    return () => document.removeEventListener("mousedown", onDoc);
  }, [open]);

  function openMenu() {
    setActive(Math.max(0, OPTIONS.findIndex((o) => o.value === value)));
    setOpen(true);
  }

  function choose(v: JobAction) {
    onChange(v);
    setOpen(false);
    btnRef.current?.focus();
  }

  function onKey(e: KeyboardEvent) {
    if (!open) {
      if (e.key === "ArrowDown" || e.key === "Enter" || e.key === " ") {
        e.preventDefault();
        openMenu();
      }
      return;
    }
    if (e.key === "Escape" || e.key === "Tab") {
      setOpen(false);
      if (e.key === "Escape") btnRef.current?.focus();
    } else if (e.key === "ArrowDown") {
      e.preventDefault();
      setActive((i) => (i + 1) % OPTIONS.length);
    } else if (e.key === "ArrowUp") {
      e.preventDefault();
      setActive((i) => (i - 1 + OPTIONS.length) % OPTIONS.length);
    } else if (e.key === "Enter" || e.key === " ") {
      e.preventDefault();
      choose(OPTIONS[active].value);
    }
  }

  return (
    <div className="action-menu" ref={rootRef} onKeyDown={onKey}>
      <button
        type="button"
        ref={btnRef}
        className={`action-trigger${open ? " open" : ""}`}
        aria-haspopup="listbox"
        aria-expanded={open}
        aria-label={`Output action: ${current.label}`}
        onClick={() => (open ? setOpen(false) : openMenu())}
      >
        <span className="at-label">{current.label}</span>
        <svg className="at-caret" viewBox="0 0 10 10" aria-hidden="true">
          <path
            d="M2 3.5 5 6.5 8 3.5"
            fill="none"
            stroke="currentColor"
            strokeWidth="1.4"
            strokeLinecap="round"
            strokeLinejoin="round"
          />
        </svg>
      </button>

      {open && (
        <ul className="action-pop" role="listbox" aria-label="Output action">
          {OPTIONS.map((o, i) => (
            <li
              key={o.value}
              role="option"
              aria-selected={o.value === value}
              className={
                "action-opt" +
                (o.value === value ? " selected" : "") +
                (i === active ? " active" : "")
              }
              onMouseEnter={() => setActive(i)}
              onMouseDown={(e) => {
                e.preventDefault(); // keep focus off the <li>, run before blur
                choose(o.value);
              }}
            >
              <span className="ao-check" aria-hidden="true">
                {o.value === value ? "✓" : ""}
              </span>
              <span className="ao-text">
                <span className="ao-label">{o.label}</span>
                <span className="ao-desc">{o.desc}</span>
              </span>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
