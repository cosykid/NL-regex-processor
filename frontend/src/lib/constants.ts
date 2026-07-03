import type { JobAction, JobStatus } from "./api-types";

export const TERMINAL = new Set<JobStatus>(["SUCCESS", "FAILED", "CANCELLED"]);

// Actions whose runs consume the typed value box. Everything else — including
// `auto`, where the AI infers any value from the prompt itself — ignores it,
// so the composer hides the box and the run payload sends it empty.
export const VALUE_ACTIONS = new Set<JobAction>(["replace", "mask"]);
