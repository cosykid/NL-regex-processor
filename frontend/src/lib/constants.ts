import type { JobStatus } from "./api-types";

export const TERMINAL = new Set<JobStatus>(["SUCCESS", "FAILED", "CANCELLED"]);
