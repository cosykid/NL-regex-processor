// Map internal pipeline stage labels to calm, user-facing phrasing (and never
// leak implementation details into the UI).
export const STAGE_LABELS: Record<string, string> = {
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

export function prettyStage(stage: string | null | undefined): string {
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
