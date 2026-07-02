import { useCallback, useEffect, useRef, useState } from "react";
import type { RefObject } from "react";
import { getUploadRows } from "../api/client";
import { errorMessage } from "../lib/errors";
import type { Dataset, GridMeta, Row } from "../lib/api-types";

// Rows fetched per lazy window when scrolling the original (pre-transform) file.
const PREVIEW_WINDOW = 100;

interface Params {
  dataset: Dataset | null;
  browsingOriginal: boolean;
  showingResult: boolean;
  scrollRef: RefObject<HTMLDivElement>;
  sentinelRef: RefObject<HTMLDivElement>;
  onMeta?: (meta: GridMeta) => void;
}

/**
 * Original-file browsing (no run selected): rows accumulate as you scroll.
 * Owns the lazy-loading state/refs for paging through the raw uploaded file a
 * window at a time, seeded from the upload's initial preview rows.
 *
 * `browsingOriginal` gates the effects that fetch more / report meta — a
 * running or failed run shows the preview as a backdrop behind an overlay, so
 * we must not fetch more there. `scrollRef`/`sentinelRef` are the DOM nodes
 * that drive the IntersectionObserver-based lazy loading.
 */
export function usePreviewRows({
  dataset,
  browsingOriginal,
  showingResult,
  scrollRef,
  sentinelRef,
  onMeta,
}: Params) {
  const [previewRows, setPreviewRows] = useState<Row[]>([]);
  const [previewDone, setPreviewDone] = useState(false); // reached end of file
  const [previewLoading, setPreviewLoading] = useState(false);
  const [previewError, setPreviewError] = useState("");

  const previewCursorRef = useRef<string | null>(null); // opaque continuation cursor from the API
  const previewStartedRef = useRef(false); // has the first real window superseded the seed?
  const previewLoadingRef = useRef(false); // guards against overlapping windows

  /* ---- seed the original-file view from the upload's initial preview ---- */
  useEffect(() => {
    setPreviewRows(dataset?.preview_rows ?? []);
    setPreviewDone(false);
    setPreviewError("");
    previewCursorRef.current = null;
    previewStartedRef.current = false;
    previewLoadingRef.current = false;
    setPreviewLoading(false);
  }, [dataset?.id]);

  // Fetch the next window of the original file. Continuation is cursor-based, so
  // each fetch reads only its own window rather than re-scanning from the top.
  // Guarded with a ref so a burst of scroll events can't fire overlapping calls.
  const loadMorePreview = useCallback(() => {
    if (!dataset || previewLoadingRef.current || previewDone) return;
    previewLoadingRef.current = true;
    setPreviewLoading(true);
    getUploadRows(dataset.id, previewCursorRef.current, PREVIEW_WINDOW)
      .then((d) => {
        const incoming = d.rows ?? [];
        // The first real window is read from the top and supersedes the seed
        // preview; every window after it appends.
        if (previewStartedRef.current) {
          setPreviewRows((prev) => [...prev, ...incoming]);
        } else {
          previewStartedRef.current = true;
          setPreviewRows(incoming);
        }
        previewCursorRef.current = d.cursor ?? null;
        if (d.eof || !d.cursor) setPreviewDone(true);
      })
      .catch((e) => {
        setPreviewError(errorMessage(e));
        setPreviewDone(true); // stop retrying a failing window
      })
      .finally(() => {
        previewLoadingRef.current = false;
        setPreviewLoading(false);
      });
  }, [dataset, previewDone]);

  /* ---- lazy-load more of the original file as the sentinel scrolls in ---- */
  useEffect(() => {
    if (!browsingOriginal || previewDone) return;
    const root = scrollRef.current;
    const el = sentinelRef.current;
    if (!root || !el) return;
    const io = new IntersectionObserver(
      (entries) => {
        if (entries.some((e) => e.isIntersecting)) loadMorePreview();
      },
      { root, rootMargin: "300px" }
    );
    io.observe(el);
    return () => io.disconnect();
  }, [browsingOriginal, previewDone, loadMorePreview, scrollRef, sentinelRef]);

  /* ---- keep filling until the pane actually scrolls (short files / tall
         viewports never trigger the sentinel on their own) ---- */
  useEffect(() => {
    if (!browsingOriginal || previewDone || previewLoading) return;
    const root = scrollRef.current;
    if (root && root.scrollHeight <= root.clientHeight) loadMorePreview();
  }, [browsingOriginal, previewDone, previewLoading, previewRows, loadMorePreview, scrollRef]);

  /* ---- report original-file meta to the status bar as rows accumulate ---- */
  useEffect(() => {
    if (showingResult) return;
    onMeta?.({
      preview: true,
      total: previewRows.length,
      shown: previewRows.length,
      eof: previewDone,
    });
  }, [showingResult, previewRows.length, previewDone, onMeta]);

  return {
    previewRows,
    previewDone,
    previewLoading,
    previewError,
  };
}
