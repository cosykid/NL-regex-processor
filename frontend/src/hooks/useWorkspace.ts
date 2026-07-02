import { useCallback, useEffect, useMemo, useState } from "react";
import { cancelJob, createJob, listJobs } from "../api/client";
import { TERMINAL } from "../lib/constants";
import { errorMessage } from "../lib/errors";
import type { Dataset, GridMeta, Job } from "../lib/api-types";
import { LS_KEY } from "./useSessionRestore";

/**
 * Owns the dataset/run workspace: the open file, its run history, the
 * currently selected run, the composer's fields (lifted so a run can populate
 * them and they survive view changes), and the grid-view state (meta/selected
 * cell). Returns everything App needs to wire up Sidebar/Composer/GridArea/
 * StatusBar.
 */
export function useWorkspace() {
  const [dataset, setDataset] = useState<Dataset | null>(null);
  const [runs, setRuns] = useState<Job[]>([]);
  const [activeRunId, setActiveRunId] = useState<string | null>(null);

  // Composer state (lifted so runs can populate it and it survives view changes).
  const [prompt, setPrompt] = useState("");
  const [replacement, setReplacement] = useState("");
  const [targets, setTargets] = useState<string[]>([]);
  const [focusSignal, setFocusSignal] = useState(0);
  const [error, setError] = useState("");

  // Grid view state.
  const [gridMeta, setGridMeta] = useState<GridMeta | null>(null);
  const [selectedCell, setSelectedCell] = useState<string | null>(null);
  const [cellRef, setCellRef] = useState("");

  const activeRun = useMemo(
    () => runs.find((r) => r.id === activeRunId) || null,
    [runs, activeRunId]
  );

  /* ---- reset the cell selection when the viewport's data changes ---- */
  useEffect(() => {
    setSelectedCell(null);
    setCellRef("");
  }, [activeRunId]);

  /* ---- handlers ---- */
  const handleDataset = useCallback(async (upload: Dataset) => {
    localStorage.setItem(LS_KEY, upload.id);
    setDataset(upload);
    setActiveRunId(null);
    setTargets([]);
    setPrompt("");
    setReplacement("");
    setError("");
    // A freshly uploaded file usually has no runs, but be robust to re-imports.
    try {
      setRuns(await listJobs(upload.id));
    } catch {
      setRuns([]);
    }
  }, []);

  const toggleTarget = useCallback((col: string) => {
    setTargets((prev) =>
      prev.includes(col) ? prev.filter((c) => c !== col) : [...prev, col]
    );
  }, []);

  async function runTransformation() {
    if (!dataset) return;
    setError("");
    const payload = {
      uploaded_file: dataset.id,
      nl_prompt: prompt.trim(),
      replacement_value: replacement,
      target_columns: targets,
    };
    try {
      const job = await createJob(payload);
      setRuns((prev) => [job, ...prev]);
      setActiveRunId(job.id);
    } catch (e) {
      setError(errorMessage(e));
    }
  }

  function selectRun(run: Job) {
    setActiveRunId(run.id);
    setPrompt(run.nl_prompt || "");
    setReplacement(run.replacement_value ?? "");
    if (Array.isArray(run.target_columns) && run.target_columns.length) {
      setTargets(run.target_columns);
    }
  }

  function newRun() {
    setActiveRunId(null);
    setPrompt("");
    setError("");
    setFocusSignal((n) => n + 1);
  }

  function importAnother() {
    if (
      runs.some((r) => !TERMINAL.has(r.status)) &&
      !window.confirm("A run is still in progress. Open a different file anyway?")
    ) {
      return;
    }
    localStorage.removeItem(LS_KEY);
    setDataset(null);
    setRuns([]);
    setActiveRunId(null);
    setTargets([]);
    setPrompt("");
    setGridMeta(null);
  }

  async function cancelRun(run: Job) {
    try {
      const updated = await cancelJob(run.id);
      setRuns((prev) => prev.map((r) => (r.id === run.id ? updated : r)));
    } catch {
      /* the next poll will reflect the state */
    }
  }

  function onSelectCell(key: string, label: string) {
    setSelectedCell(key);
    setCellRef(label);
  }

  return {
    dataset,
    setDataset,
    runs,
    setRuns,
    activeRunId,
    activeRun,
    prompt,
    setPrompt,
    replacement,
    setReplacement,
    targets,
    focusSignal,
    error,
    gridMeta,
    setGridMeta,
    selectedCell,
    cellRef,
    handleDataset,
    toggleTarget,
    runTransformation,
    selectRun,
    newRun,
    importAnother,
    cancelRun,
    onSelectCell,
  };
}
