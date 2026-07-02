import { useEffect, useState } from "react";
import type { Dispatch, SetStateAction } from "react";
import { getUpload, listJobs } from "../api/client";
import type { Dataset, Job } from "../lib/api-types";

export const LS_KEY = "ds.activeId";

interface Params {
  setDataset: Dispatch<SetStateAction<Dataset | null>>;
  setRuns: Dispatch<SetStateAction<Job[]>>;
}

/**
 * Restores a previous session from localStorage on mount: if a dataset id was
 * persisted, re-fetch the upload + its run history before rendering the
 * workspace. Returns `restoring` so the caller can show a loading state until
 * this settles.
 */
export function useSessionRestore({ setDataset, setRuns }: Params): boolean {
  const [restoring, setRestoring] = useState(
    () => !!localStorage.getItem(LS_KEY)
  );

  useEffect(() => {
    const id = localStorage.getItem(LS_KEY);
    if (!id) {
      setRestoring(false);
      return;
    }
    let active = true;
    (async () => {
      try {
        const [up, jobs] = await Promise.all([getUpload(id), listJobs(id)]);
        if (!active) return;
        setDataset(up);
        setRuns(jobs);
      } catch {
        localStorage.removeItem(LS_KEY);
      } finally {
        if (active) setRestoring(false);
      }
    })();
    return () => {
      active = false;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  return restoring;
}
