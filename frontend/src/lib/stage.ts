// Map internal pipeline stage labels to calm, user-facing phrasing (and never
// leak implementation details into the UI). Keys mirror the stages the backend
// actually emits (processing/tasks.py + spark_engine.py), lowercased.
export const STAGE_LABELS: Record<string, string> = {
  queued: "Queued",
  "generating regex": "Understanding your request",
  "regex ready": "Pattern ready",
  "starting spark engine": "Starting the engine",
  "reading file into spark": "Reading your file",
  "scanning rows": "Finding matches",
  finalising: "Finishing up",
  finalizing: "Finishing up",
  completed: "Completed",
  cancelling: "Cancelling",
  cancelled: "Cancelled",
  failed: "Failed",
};

// The write stage is per-action ("applying mask (Spark write)"), so it can't be
// a fixed key above; map the action word to its user-facing verb instead.
const APPLYING_LABELS: Record<string, string> = {
  find: "Marking matches",
  replace: "Applying changes",
  mask: "Masking matches",
  extract: "Extracting matches",
  keep: "Filtering rows",
  drop: "Removing rows",
};

export function prettyStage(stage: string | null | undefined): string {
  if (!stage) return "Working";
  const key = String(stage).trim().toLowerCase();
  if (STAGE_LABELS[key]) return STAGE_LABELS[key];
  const applying = key.match(/^applying (\w+)/);
  if (applying && APPLYING_LABELS[applying[1]]) return APPLYING_LABELS[applying[1]];
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
