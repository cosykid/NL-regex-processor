import { useEffect } from "react";
import ImportView from "./components/ImportView";
import Sidebar from "./components/Sidebar";
import Composer from "./components/Composer";
import GridArea from "./components/grid/GridArea";
import StatusBar from "./components/StatusBar";
import ThemeToggle from "./components/ThemeToggle";
import { useTheme } from "./hooks/useTheme";
import { useSessionRestore } from "./hooks/useSessionRestore";
import { useJobPolling } from "./hooks/useJobPolling";
import { useWorkspace } from "./hooks/useWorkspace";

export default function App() {
  const { theme, toggleTheme } = useTheme();

  const {
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
    action,
    setAction,
    targets,
    focusSignal,
    error,
    gridMeta,
    setGridMeta,
    selectedCell,
    cellRef,
    handleDataset,
    reopen,
    toggleTarget,
    runTransformation,
    selectRun,
    newRun,
    importAnother,
    cancelRun,
    onSelectCell,
  } = useWorkspace();

  const restoring = useSessionRestore({ setDataset, setRuns });

  // A file dropped outside a real drop target otherwise makes the browser
  // navigate to it (looks like the app "ate" the file). Swallow those.
  useEffect(() => {
    const prevent = (e: DragEvent) => e.preventDefault();
    window.addEventListener("dragover", prevent);
    window.addEventListener("drop", prevent);
    return () => {
      window.removeEventListener("dragover", prevent);
      window.removeEventListener("drop", prevent);
    };
  }, []);

  useJobPolling(runs, setRuns);

  // Name the tab after the open file (matches index.html's default otherwise).
  useEffect(() => {
    document.title = dataset
      ? `${dataset.original_name} — Find & replace`
      : "Find & replace across your data";
  }, [dataset]);

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
        <ImportView onDataset={handleDataset} onReopen={reopen} />
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
            action={action}
            setAction={setAction}
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
