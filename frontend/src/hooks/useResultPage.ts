import { useCallback, useEffect, useRef, useState } from "react";
import type { FormEvent, RefObject } from "react";
import { getResults } from "../api/client";
import { errorMessage } from "../lib/errors";
import * as resultCache from "../lib/resultCache";
import type { GridMeta, Job, ResultsResponse, Row } from "../lib/api-types";

export const PAGE_SIZE = 100;

interface Params {
  activeRun: Job | null;
  showingResult: boolean;
  scrollRef: RefObject<HTMLDivElement>;
  onMeta?: (meta: GridMeta) => void;
}

/**
 * Result paging: once a run has succeeded, the grid pages through its output
 * a window at a time — Prev/Next or a direct jump-to-row, so any row in a
 * million-row result is one step away without scrolling through everything.
 * Rows the pattern affected can be isolated with an affected-only view.
 */
export function useResultPage({ activeRun, showingResult, scrollRef, onMeta }: Params) {
  const [columns, setColumns] = useState<string[] | null>(null);
  const [rows, setRows] = useState<Row[]>([]);
  const [total, setTotal] = useState(0); // rows in the current view
  const [totalAll, setTotalAll] = useState(0);
  const [matchedTotal, setMatchedTotal] = useState(0);
  const [hasFlag, setHasFlag] = useState(false);
  const [affectedOnly, setAffectedOnly] = useState(false);
  const [page, setPage] = useState(1);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const [jumpText, setJumpText] = useState("");
  const [flashIndex, setFlashIndex] = useState<number | null>(null);
  const [openRow, setOpenRow] = useState<number | null>(null); // row index (in page) shown in detail panel

  const reqToken = useRef(0); // invalidates in-flight requests on run/page switch
  const pendingFlash = useRef<number | null>(null); // row index to flash once a page lands
  const flashTimer = useRef<ReturnType<typeof setTimeout> | undefined>(undefined);

  const numPages = Math.max(1, Math.ceil(total / PAGE_SIZE));
  const rowOffset = (page - 1) * PAGE_SIZE;

  /* ---- reset when the active result changes ---- */
  useEffect(() => {
    setAffectedOnly(false);
    setPage(1);
    setJumpText("");
    pendingFlash.current = null;
  }, [activeRun?.id]);

  const flashAndScroll = useCallback(
    (idx: number | null) => {
      const root = scrollRef.current;
      if (!root || idx == null) return;
      const el = root.querySelector(`tr[data-ri="${idx}"]`);
      if (el) el.scrollIntoView({ block: "center", behavior: "smooth" });
      setFlashIndex(idx);
      clearTimeout(flashTimer.current);
      flashTimer.current = setTimeout(() => setFlashIndex(null), 1700);
    },
    [scrollRef]
  );

  /* ---- (re)load whenever the result, page, or filter changes ---- */
  useEffect(() => {
    const token = ++reqToken.current;
    setOpenRow(null); // a new set of rows invalidates the open detail

    if (!showingResult || !activeRun) {
      // Clear any stale result state; the original-file view owns its own rows
      // and reports its own meta (see usePreviewRows).
      setColumns(null);
      setRows([]);
      setTotal(0);
      setTotalAll(0);
      setMatchedTotal(0);
      setHasFlag(false);
      setError("");
      setLoading(false);
      return;
    }

    // Push one response's data into state and reproduce the post-load side
    // effects (scroll reset, jump-to-row flash). Shared by the cache-hit and
    // fetch-success paths so a hit lands identically to a fetch, minus the wait.
    const applyData = (d: ResultsResponse) => {
      setColumns(d.columns);
      setRows(d.rows);
      setTotal(d.total);
      setTotalAll(d.total_all ?? d.total);
      setMatchedTotal(d.matched_total ?? 0);
      setHasFlag(!!d.has_match_flag);
      onMeta?.({
        preview: false,
        total: d.total,
        totalAll: d.total_all ?? d.total,
        matchedTotal: d.matched_total ?? 0,
        affectedOnly,
        shown: d.rows.length,
        page: d.page,
        numPages: d.num_pages,
      });
      if (scrollRef.current) scrollRef.current.scrollTop = 0;
      // If this load was triggered by a jump, flash the target row.
      if (pendingFlash.current != null) {
        const idx = pendingFlash.current;
        pendingFlash.current = null;
        requestAnimationFrame(() => flashAndScroll(idx));
      }
    };

    // A previously fetched page is immutable within the session — serve it from
    // memory with no spinner and no network call.
    const key = resultCache.keyFor(activeRun.id, page, affectedOnly);
    const cached = resultCache.get(key);
    if (cached) {
      setError("");
      setLoading(false);
      applyData(cached);
      return;
    }

    setLoading(true);
    setError("");

    getResults(activeRun.id, page, PAGE_SIZE, affectedOnly)
      .then((d) => {
        if (token !== reqToken.current) return;
        resultCache.set(key, d);
        applyData(d);
      })
      .catch((e) => {
        if (token === reqToken.current) setError(errorMessage(e));
      })
      .finally(() => {
        if (token === reqToken.current) setLoading(false);
      });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [showingResult, activeRun?.id, page, affectedOnly]);

  useEffect(() => () => clearTimeout(flashTimer.current), []);

  const goToRow = useCallback(
    (n: number) => {
      if (!Number.isFinite(n) || total === 0) return;
      const target = Math.max(1, Math.min(total, Math.floor(n)));
      const p = Math.floor((target - 1) / PAGE_SIZE) + 1;
      const localIdx = (target - 1) % PAGE_SIZE;
      if (p === page) {
        flashAndScroll(localIdx);
      } else {
        pendingFlash.current = localIdx;
        setPage(p);
      }
    },
    [total, page, flashAndScroll]
  );

  function submitJump(e: FormEvent) {
    e.preventDefault();
    const n = parseInt(jumpText, 10);
    if (Number.isFinite(n)) goToRow(n);
  }

  function toggleAffected() {
    if (!hasFlag) return;
    pendingFlash.current = null;
    setPage(1);
    setAffectedOnly((v) => !v);
  }

  const toggleRow = useCallback(
    (i: number) => setOpenRow((prev) => (prev === i ? null : i)),
    []
  );

  return {
    columns,
    rows,
    total,
    totalAll,
    matchedTotal,
    hasFlag,
    affectedOnly,
    page,
    setPage,
    loading,
    error,
    jumpText,
    setJumpText,
    flashIndex,
    openRow,
    numPages,
    rowOffset,
    goToRow,
    submitJump,
    toggleAffected,
    toggleRow,
    setOpenRow,
  };
}
