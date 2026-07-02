// Thin API client. In dev, Vite proxies /api -> Django; in prod, nginx does.
const BASE = import.meta.env.VITE_API_BASE || "/api";

async function handle(res) {
  if (!res.ok) {
    let detail = `Request failed (${res.status})`;
    try {
      const body = await res.json();
      detail = body.detail || JSON.stringify(body);
    } catch {
      /* keep default */
    }
    throw new Error(detail);
  }
  return res.json();
}

export async function uploadFile(file) {
  const form = new FormData();
  form.append("file", file);
  return handle(await fetch(`${BASE}/uploads`, { method: "POST", body: form }));
}

export async function createJob(payload) {
  return handle(
    await fetch(`${BASE}/jobs`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    })
  );
}

export async function getUpload(id) {
  return handle(await fetch(`${BASE}/uploads/${id}`));
}

// A window of the raw uploaded file — lets the grid scroll through the original
// dataset before any transformation has been applied. Continuation is
// cursor-based: pass back the `cursor` from the previous window (null for the
// first). Returns {rows, eof, cursor}.
export async function getUploadRows(id, cursor, limit) {
  const qs = new URLSearchParams({ limit });
  if (cursor != null) qs.set("cursor", cursor);
  return handle(await fetch(`${BASE}/uploads/${id}/rows?${qs}`));
}

export async function getJob(id) {
  return handle(await fetch(`${BASE}/jobs/${id}`));
}

// Run history for a single dataset — a dataset can be transformed any number
// of times, and each run is listed here.
export async function listJobs(uploadId) {
  const qs = new URLSearchParams({ uploaded_file: uploadId, page_size: 100 });
  const data = await handle(await fetch(`${BASE}/jobs?${qs}`));
  return data.results ?? data; // paginated -> {results} ; be tolerant
}

export async function cancelJob(id) {
  return handle(await fetch(`${BASE}/jobs/${id}/cancel`, { method: "POST" }));
}

export async function getResults(id, page, pageSize, matchedOnly = false) {
  const qs = new URLSearchParams({ page, page_size: pageSize });
  if (matchedOnly) qs.set("matched_only", "true");
  return handle(await fetch(`${BASE}/jobs/${id}/results?${qs}`));
}

// Direct URL for an export download (navigated to by an <a download>).
// `format` is "csv" (default) or "xlsx". The query key is `fmt`, not `format`:
// DRF reserves `?format=` for content negotiation and 404s on unknown values.
export function exportUrl(id, { matchedOnly = false, format = "csv" } = {}) {
  const qs = new URLSearchParams();
  if (matchedOnly) qs.set("matched_only", "true");
  if (format && format !== "csv") qs.set("fmt", format);
  const q = qs.toString();
  return `${BASE}/jobs/${id}/export${q ? `?${q}` : ""}`;
}
