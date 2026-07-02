// Small presentation helpers shared across the workspace.

export const TERMINAL = new Set(["SUCCESS", "FAILED", "CANCELLED"]);

export function fmtInt(n) {
  if (n === null || n === undefined) return "—";
  return Number(n).toLocaleString();
}

export function fmtBytes(b) {
  if (!b && b !== 0) return "—";
  const u = ["B", "KB", "MB", "GB", "TB"];
  let i = 0;
  let n = b;
  while (n >= 1024 && i < u.length - 1) {
    n /= 1024;
    i += 1;
  }
  return `${n < 10 && i > 0 ? n.toFixed(1) : Math.round(n)} ${u[i]}`;
}

// Map internal pipeline stage labels to calm, user-facing phrasing (and never
// leak implementation details into the UI).
const STAGE_LABELS = {
  queued: "Queued",
  "generating regex": "Understanding your request",
  "regex ready": "Pattern ready",
  "reading file into spark": "Reading your file",
  "counting rows": "Counting rows",
  "scanning for matches": "Finding matches",
  "applying replacement (spark write)": "Applying changes",
  finalising: "Finishing up",
  finalizing: "Finishing up",
  completed: "Completed",
  cancelling: "Cancelling",
  cancelled: "Cancelled",
  failed: "Failed",
};

export function prettyStage(stage) {
  if (!stage) return "Working";
  const key = String(stage).trim().toLowerCase();
  if (STAGE_LABELS[key]) return STAGE_LABELS[key];
  // Fallback: strip any parenthetical and implementation-flavored words, then
  // present a clean, capitalized phrase.
  let s = String(stage)
    .replace(/\s*\([^)]*\)\s*/g, " ")
    .replace(/\b(spark|parquet|regex|duckdb|celery|redis)\b/gi, "")
    .replace(/\s{2,}/g, " ")
    .trim();
  if (!s) s = "Working";
  return s.charAt(0).toUpperCase() + s.slice(1);
}

export function timeAgo(iso) {
  if (!iso) return "";
  const s = Math.max(0, (Date.now() - new Date(iso).getTime()) / 1000);
  if (s < 5) return "just now";
  if (s < 60) return `${Math.floor(s)}s ago`;
  if (s < 3600) return `${Math.floor(s / 60)}m ago`;
  if (s < 86400) return `${Math.floor(s / 3600)}h ago`;
  return `${Math.floor(s / 86400)}d ago`;
}

const EMAIL = /^[^@\s]+@[^@\s]+\.[^@\s]+$/;
const URLISH = /^https?:\/\/|^www\./i;
const NUMISH = /^-?\$?\d[\d,]*\.?\d*%?$/;
const DATEISH = /^\d{4}[-/]\d{1,2}[-/]\d{1,2}|^\d{1,2}[-/]\d{1,2}[-/]\d{2,4}/;

// Lightweight column-type inference (Airtable-style header glyphs) from the
// sampled preview values — purely cosmetic, never affects processing.
export function inferType(name, samples) {
  const vals = (samples || [])
    .map((v) => (v === null || v === undefined ? "" : String(v).trim()))
    .filter(Boolean)
    .slice(0, 8);
  const lname = (name || "").toLowerCase();
  const all = (re) => vals.length > 0 && vals.every((v) => re.test(v));

  if (lname.includes("email") || all(EMAIL)) return { glyph: "@", title: "Email" };
  if (lname.includes("url") || lname.includes("link") || all(URLISH))
    return { glyph: "↗", title: "URL" };
  if (
    lname.includes("date") ||
    lname.includes("time") ||
    lname.endsWith("_at") ||
    all(DATEISH)
  )
    return { glyph: "◷", title: "Date / time" };
  if (lname === "id" || lname.endsWith("_id") || lname.includes("phone"))
    return { glyph: "#", title: "Identifier / number" };
  if (all(NUMISH)) return { glyph: "#", title: "Number" };
  return { glyph: "T", title: "Text" };
}
