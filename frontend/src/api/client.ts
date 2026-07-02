// Thin API client. In dev, Vite proxies /api -> Django; in prod, nginx does.
import type {
  Dataset,
  Job,
  JobCreatePayload,
  ResultsResponse,
  UploadRowsResponse,
} from "../lib/api-types";

const BASE = import.meta.env.VITE_API_BASE || "/api";

async function handle<T>(res: Response): Promise<T> {
  if (!res.ok) {
    let detail = `Request failed (${res.status})`;
    try {
      const body = (await res.json()) as { detail?: string };
      detail = body.detail || JSON.stringify(body);
    } catch {
      /* keep default */
    }
    throw new Error(detail);
  }
  return res.json() as Promise<T>;
}

export async function uploadFile(file: File): Promise<Dataset> {
  const form = new FormData();
  form.append("file", file);
  return handle<Dataset>(
    await fetch(`${BASE}/uploads`, { method: "POST", body: form })
  );
}

export async function createJob(payload: JobCreatePayload): Promise<Job> {
  return handle<Job>(
    await fetch(`${BASE}/jobs`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    })
  );
}

export async function getUpload(id: string): Promise<Dataset> {
  return handle<Dataset>(await fetch(`${BASE}/uploads/${id}`));
}

// A window of the raw uploaded file — lets the grid scroll through the original
// dataset before any transformation has been applied. Continuation is
// cursor-based: pass back the `cursor` from the previous window (null for the
// first). Returns {rows, eof, cursor}.
export async function getUploadRows(
  id: string,
  cursor: string | null,
  limit: number
): Promise<UploadRowsResponse> {
  const qs = new URLSearchParams({ limit: String(limit) });
  if (cursor != null) qs.set("cursor", cursor);
  return handle<UploadRowsResponse>(
    await fetch(`${BASE}/uploads/${id}/rows?${qs}`)
  );
}

export async function getJob(id: string): Promise<Job> {
  return handle<Job>(await fetch(`${BASE}/jobs/${id}`));
}

// Run history for a single dataset — a dataset can be transformed any number
// of times, and each run is listed here.
export async function listJobs(uploadId: string): Promise<Job[]> {
  const qs = new URLSearchParams({
    uploaded_file: uploadId,
    page_size: "100",
  });
  const data = await handle<{ results?: Job[] } | Job[]>(
    await fetch(`${BASE}/jobs?${qs}`)
  );
  // paginated -> {results} ; be tolerant of a bare array
  return Array.isArray(data) ? data : data.results ?? [];
}

export async function cancelJob(id: string): Promise<Job> {
  return handle<Job>(
    await fetch(`${BASE}/jobs/${id}/cancel`, { method: "POST" })
  );
}

export async function getResults(
  id: string,
  page: number,
  pageSize: number,
  matchedOnly = false
): Promise<ResultsResponse> {
  const qs = new URLSearchParams({ page: String(page), page_size: String(pageSize) });
  if (matchedOnly) qs.set("matched_only", "true");
  return handle<ResultsResponse>(
    await fetch(`${BASE}/jobs/${id}/results?${qs}`)
  );
}

// Direct URL for an export download (navigated to by an <a download>).
// `format` is "csv" (default) or "xlsx". The query key is `fmt`, not `format`:
// DRF reserves `?format=` for content negotiation and 404s on unknown values.
export function exportUrl(
  id: string,
  { matchedOnly = false, format = "csv" }: { matchedOnly?: boolean; format?: "csv" | "xlsx" } = {}
): string {
  const qs = new URLSearchParams();
  if (matchedOnly) qs.set("matched_only", "true");
  if (format && format !== "csv") qs.set("fmt", format);
  const q = qs.toString();
  return `${BASE}/jobs/${id}/export${q ? `?${q}` : ""}`;
}
