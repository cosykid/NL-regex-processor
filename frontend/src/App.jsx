import { useCallback, useEffect, useMemo, useState } from "react";
import { cancelJob, createJob, getJob, getUpload, listJobs } from "./api";
import { TERMINAL } from "./util";
import ImportView from "./components/ImportView";
import Sidebar from "./components/Sidebar";
import Composer from "./components/FormulaBar";
import GridArea from "./components/GridArea";
import StatusBar from "./components/StatusBar";
import ThemeToggle from "./components/ThemeToggle";

const POLL_MS = 1400;
const LS_KEY = "ds.activeId";
const THEME_KEY = "ds.theme";

function initialTheme() {
  const saved = localStorage.getItem(THEME_KEY);
  if (saved === "light" || saved === "dark") return saved;
  return window.matchMedia?.("(prefers-color-scheme: dark)").matches
    ? "dark"
    : "light";
}

export default function App() {
  const [restoring, setRestoring] = useState(
    () => !!localStorage.getItem(LS_KEY)
  );
  const [theme, setTheme] = useState(initialTheme);
  const [dataset, setDataset] = useState(null);
  const [runs, setRuns] = useState([]);
  const [activeRunId, setActiveRunId] = useState(null);

  // Composer state (lifted so runs can populate it and it survives view changes).
  const [prompt, setPrompt] = useState("");
  const [replacement, setReplacement] = useState("");
  const [targets, setTargets] = useState([]);
  const [focusSignal, setFocusSignal] = useState(0);
  const [error, setError] = useState("");

  // Grid view state.
  const [gridMeta, setGridMeta] = useState(null);
  const [selectedCell, setSelectedCell] = useState(null);
  const [cellRef, setCellRef] = useState("");

  const activeRun = useMemo(
    () => runs.find((r) => r.id === activeRunId) || null,
    [runs, activeRunId]
  );

  /* ---- apply + persist the colour theme ---- */
  useEffect(() => {
    document.documentElement.dataset.theme = theme;
    localStorage.setItem(THEME_KEY, theme);
    const meta = document.querySelector('meta[name="theme-color"]');
    if (meta) meta.setAttribute("content", theme === "dark" ? "#0b0e15" : "#f5f6f8");
  }, [theme]);

  const toggleTheme = useCallback(
    () => setTheme((t) => (t === "dark" ? "light" : "dark")),
    []
  );

  /* ---- restore a previous session from localStorage ---- */
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
  }, []);

  /* ---- poll every non-terminal run until it settles ---- */
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

  /* ---- reset the cell selection when the viewport's data changes ---- */
  useEffect(() => {
    setSelectedCell(null);
    setCellRef("");
  }, [activeRunId]);

  /* ---- handlers ---- */
  const handleDataset = useCallback(async (upload) => {
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

  const toggleTarget = useCallback((col) => {
    setTargets((prev) =>
      prev.includes(col) ? prev.filter((c) => c !== col) : [...prev, col]
    );
  }, []);

  async function runTransformation() {
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
      setError(e.message);
    }
  }

  function selectRun(run) {
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

  async function cancelRun(run) {
    try {
      const updated = await cancelJob(run.id);
      setRuns((prev) => prev.map((r) => (r.id === run.id ? updated : r)));
    } catch {
      /* the next poll will reflect the state */
    }
  }

  function onSelectCell(key, label) {
    setSelectedCell(key);
    setCellRef(label);
  }

  /* ---- render ---- */
  if (restoring) {
    return (
      <div className="app">
        <ThemeToggle theme={theme} onToggle={toggleTheme} className="floating" />
        <div className="import-wrap">
          <span className="spinner lg" />
        </div>
      </div>
    );
  }

  if (!dataset) {
    return (
      <div className="app">
        <ThemeToggle theme={theme} onToggle={toggleTheme} className="floating" />
        <ImportView onDataset={handleDataset} />
      </div>
    );
  }

  return (
    <div className="app">
      <div className="workspace">
        <Sidebar
          dataset={dataset}
          runs={runs}
          activeRunId={activeRunId}
          onSelectRun={selectRun}
          onNewRun={newRun}
          onImportAnother={importAnother}
        />
        <div className="canvas">
          <Composer
            prompt={prompt}
            setPrompt={setPrompt}
            replacement={replacement}
            setReplacement={setReplacement}
            targets={targets}
            onToggleTarget={toggleTarget}
            onRun={runTransformation}
            activeRun={activeRun}
            focusSignal={focusSignal}
            error={error}
          />
          <GridArea
            dataset={dataset}
            activeRun={activeRun}
            targets={targets}
            onToggleTarget={toggleTarget}
            onMeta={setGridMeta}
            selectedCell={selectedCell}
            onSelectCell={onSelectCell}
            onCancel={cancelRun}
          />
          <StatusBar
            activeRun={activeRun}
            targets={targets}
            gridMeta={gridMeta}
            cellRef={cellRef}
            theme={theme}
            onToggleTheme={toggleTheme}
          />
        </div>
      </div>
    </div>
  );
}
