import { useEffect } from "react";
import type { Dispatch, SetStateAction } from "react";
import { getJob } from "../api/client";
import { TERMINAL } from "../lib/constants";
import type { Job } from "../lib/api-types";

const POLL_MS = 1400;

/** Polls every non-terminal run on an interval until it settles. */
export function useJobPolling(
  runs: Job[],
  setRuns: Dispatch<SetStateAction<Job[]>>
) {
  useEffect(() => {
    const live = runs.filter((r) => !TERMINAL.has(r.status));
    if (live.length === 0) return;
    let active = true;
    const t = setTimeout(async () => {
      const updated = await Promise.all(
        live.map((r) => getJob(r.id).catch(() => null))
      );
      if (!active) return;
      setRuns((prev) =>
        prev.map((r) => updated.find((u) => u && u.id === r.id) || r)
      );
    }, POLL_MS);
    return () => {
      active = false;
      clearTimeout(t);
    };
  }, [runs]);
}
