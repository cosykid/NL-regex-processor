import type { Theme } from "../lib/api-types";

interface Props {
  theme: Theme;
  onToggle: () => void;
  className?: string;
}

/**
 * Light/dark switch. Reused in two spots: docked at the right edge of the
 * status bar in the workspace, and floated top-right on the standalone
 * import / loading screens (pass className="floating").
 */
export default function ThemeToggle({ theme, onToggle, className = "" }: Props) {
  const dark = theme === "dark";
  return (
    <button
      type="button"
      className={`theme-toggle ${className}`.trim()}
      onClick={onToggle}
      title={dark ? "Switch to light mode" : "Switch to dark mode"}
      aria-label={dark ? "Switch to light mode" : "Switch to dark mode"}
      aria-pressed={dark}
    >
      {dark ? (
        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
          <circle cx="12" cy="12" r="4" />
          <path
            d="M12 2v2M12 20v2M4.9 4.9l1.4 1.4M17.7 17.7l1.4 1.4M2 12h2M20 12h2M4.9 19.1l1.4-1.4M17.7 6.3l1.4-1.4"
            strokeLinecap="round"
          />
        </svg>
      ) : (
        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
          <path
            d="M21 12.8A9 9 0 1 1 11.2 3a7 7 0 0 0 9.8 9.8Z"
            strokeLinejoin="round"
          />
        </svg>
      )}
    </button>
  );
}
