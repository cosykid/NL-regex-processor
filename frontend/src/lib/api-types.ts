// Domain types shared across the API client, hooks, and components. These mirror
// the DRF serializers in backend/api/serializers.py and the paged reads in
// backend/processing/results.py.

export type JobStatus =
  | "QUEUED"
  | "RUNNING"
  | "SUCCESS"
  | "FAILED"
  | "CANCELLED";

export type RegexSource = "cache" | "llm" | "heuristic" | "";

/** One data row. Cell values arrive as strings (CSV) or native scalars (Parquet
 *  results via DuckDB); the two `__…__` keys are appended by the result reader. */
export interface Row {
  [column: string]: unknown;
  /** True when at least one target column matched the pattern (result rows). */
  __matched__?: boolean;
  /** The row's original (full-view) 1-based index, preserved across filtering. */
  __rownum?: number;
}

/** An uploaded file plus its header inspection — the `dataset` in the workspace.
 *  `preview_rows` is returned inline by the upload endpoint (not persisted). */
export interface Dataset {
  id: string;
  original_name: string;
  kind: string; // "csv" | "excel"
  size_bytes: number;
  columns: string[];
  created_at: string;
  preview_rows?: Row[];
}

/** The brief upload shape nested inside a Job (UploadedFileBriefSerializer). */
export interface UploadedFileBrief {
  id: string;
  original_name: string;
  columns: string[];
}

/** One natural-language replacement run against a dataset. */
export interface Job {
  id: string;
  uploaded_file: UploadedFileBrief;
  nl_prompt: string;
  replacement_value: string;
  target_columns: string[];
  status: JobStatus;
  progress: number;
  stage: string;
  regex_pattern: string;
  regex_source: RegexSource;
  regex_explanation: string;
  total_rows: number | null;
  matched_rows: number | null;
  result_columns: string[];
  error_message: string;
  created_at: string;
  updated_at: string;
}

/** POST /jobs body. */
export interface JobCreatePayload {
  uploaded_file: string;
  nl_prompt: string;
  replacement_value: string;
  target_columns: string[];
}

/** GET /uploads/<id>/rows — one cursor-based window of the raw upload. */
export interface UploadRowsResponse {
  rows: Row[];
  eof: boolean;
  cursor: string | null;
  limit?: number;
}

/** GET /jobs/<id>/results — one page of a processed result. */
export interface ResultsResponse {
  columns: string[];
  rows: Row[];
  total: number;
  total_all: number;
  matched_total: number;
  has_match_flag: boolean;
  matched_only: boolean;
  page: number;
  page_size: number;
  num_pages: number;
}

/** Cosmetic per-column type inferred for the grid header glyphs. */
export interface ColumnType {
  glyph: string;
  title: string;
}

/** Snapshot the grid reports up to the status bar. Some fields only apply to a
 *  result view, others only to the original-file (preview) view. */
export interface GridMeta {
  preview: boolean;
  total: number;
  shown: number;
  eof?: boolean;
  totalAll?: number;
  matchedTotal?: number;
  affectedOnly?: boolean;
  page?: number;
  numPages?: number;
}

export type Theme = "light" | "dark";
