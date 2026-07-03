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

type PresignResponse =
  | { mode: "direct" }
  | { mode: "s3"; id: string; url: string };

type MultipartCreateResponse = {
  id: string;
  part_size: number;
  parts: { part_number: number; url: string }[];
};

// Above this size an S3 upload is split into parts and uploaded in parallel so
// one TCP stream's bandwidth-delay product doesn't cap throughput on a
// high-latency link. Smaller files stay on the single-PUT path.
const MULTIPART_THRESHOLD = 16 * 1024 * 1024;
// Parts in flight at once. Bounded so we overlap enough streams to fill the
// pipe without opening an unbounded number of sockets.
const MULTIPART_CONCURRENCY = 6;
// Retry a failed part this many times before giving up and aborting.
const MULTIPART_PART_RETRIES = 2;

// Open a file. `onProgress` gets a 0..1 fraction that tracks the real bytes
// transferred. On S3 the browser uploads straight to storage (one transfer,
// its bytes never pass through the API), so the bar is honest end to end; the
// server then only reads the header. On the local backend there's no
// browser-reachable target, so we fall back to a multipart POST.
export async function uploadFile(
  file: File,
  onProgress?: (fraction: number) => void
): Promise<Dataset> {
  const pre = await handle<PresignResponse>(
    await fetch(`${BASE}/uploads/presign`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ filename: file.name }),
    })
  );

  if (pre.mode === "direct") return uploadMultipart(file, onProgress);

  // Large files: slice and upload the parts in parallel so throughput isn't
  // capped by a single stream. Small files keep the one-shot PUT below.
  if (file.size > MULTIPART_THRESHOLD) return uploadS3Multipart(file, onProgress);

  // PUT straight to S3 (0..0.97 real progress), then let the server inspect the
  // header. The last sliver covers that header read, filled to 1 on completion.
  await putToStorage(pre.url, file, onProgress);
  const dataset = await handle<Dataset>(
    await fetch(`${BASE}/uploads/complete`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ id: pre.id }),
    })
  );
  onProgress?.(1);
  return dataset;
}

// Direct browser->S3 PUT with real upload progress (fetch can't emit it).
function putToStorage(
  url: string,
  file: File,
  onProgress?: (fraction: number) => void
): Promise<void> {
  return new Promise<void>((resolve, reject) => {
    const xhr = new XMLHttpRequest();
    xhr.open("PUT", url);
    xhr.upload.onprogress = (e) => {
      if (onProgress && e.lengthComputable) {
        onProgress(Math.min(0.97, e.loaded / e.total));
      }
    };
    xhr.onload = () =>
      xhr.status >= 200 && xhr.status < 300
        ? resolve()
        : reject(new Error(`Upload to storage failed (${xhr.status})`));
    xhr.onerror = () => reject(new Error("Network error during upload."));
    xhr.send(file);
  });
}

// Parallel S3 multipart upload. Ask the server to open the upload and hand
// back a presigned URL per part; slice the file and PUT the parts through a
// bounded worker pool, reading each part's ETag off the response; then complete
// (or abort, on failure). Progress is the real sum of bytes across all parts.
async function uploadS3Multipart(
  file: File,
  onProgress?: (fraction: number) => void
): Promise<Dataset> {
  const created = await handle<MultipartCreateResponse>(
    await fetch(`${BASE}/uploads/multipart/create`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ filename: file.name, size: file.size }),
    })
  );
  const { id, part_size, parts } = created;

  // Per-part uploaded byte counts; summed into an honest 0..0.97 bar. The last
  // sliver covers the server-side header read, filled to 1 on completion.
  const loaded = new Array<number>(parts.length).fill(0);
  const etags = new Array<string>(parts.length).fill("");
  const emit = () => {
    if (!onProgress) return;
    const total = loaded.reduce((a, b) => a + b, 0);
    onProgress(Math.min(0.97, total / file.size));
  };

  // parts[i] (0-based) is S3 part_number i+1 and covers bytes [i*part_size, …).
  const uploadPart = async (i: number): Promise<void> => {
    const start = i * part_size;
    const blob = file.slice(start, Math.min(start + part_size, file.size));
    for (let attempt = 0; ; attempt++) {
      try {
        etags[i] = await putPart(parts[i].url, blob, (bytes) => {
          loaded[i] = bytes;
          emit();
        });
        return;
      } catch (err) {
        loaded[i] = 0; // don't credit a failed attempt's bytes
        emit();
        if (attempt >= MULTIPART_PART_RETRIES) throw err;
      }
    }
  };

  // Bounded worker pool: each worker pulls the next unclaimed part index.
  let next = 0;
  const worker = async (): Promise<void> => {
    for (let i = next++; i < parts.length; i = next++) {
      await uploadPart(i);
    }
  };
  const poolSize = Math.min(MULTIPART_CONCURRENCY, parts.length);

  try {
    await Promise.all(Array.from({ length: poolSize }, worker));
  } catch (err) {
    await abortMultipart(id);
    throw err;
  }

  const dataset = await handle<Dataset>(
    await fetch(`${BASE}/uploads/multipart/complete`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        id,
        parts: parts.map((p, i) => ({
          part_number: p.part_number,
          etag: etags[i],
        })),
      }),
    })
  );
  onProgress?.(1);
  return dataset;
}

// PUT one part to its presigned URL; resolve with the part's ETag (needed to
// complete the upload). The ETag response header must be CORS-exposed — the
// bucket's cors_rule sets expose_headers = ["ETag"].
function putPart(
  url: string,
  blob: Blob,
  onBytes: (bytes: number) => void
): Promise<string> {
  return new Promise<string>((resolve, reject) => {
    const xhr = new XMLHttpRequest();
    xhr.open("PUT", url);
    xhr.upload.onprogress = (e) => {
      if (e.lengthComputable) onBytes(e.loaded);
    };
    xhr.onload = () => {
      if (xhr.status < 200 || xhr.status >= 300) {
        reject(new Error(`Part upload failed (${xhr.status})`));
        return;
      }
      const etag = xhr.getResponseHeader("ETag");
      if (!etag) {
        reject(new Error("Upload part response missing ETag header."));
        return;
      }
      onBytes(blob.size);
      resolve(etag);
    };
    xhr.onerror = () => reject(new Error("Network error during part upload."));
    xhr.send(blob);
  });
}

// Best-effort cancel so abandoned parts don't linger (a bucket lifecycle rule
// is the backstop). Never throws — the caller is already rejecting.
function abortMultipart(id: string): Promise<void> {
  return fetch(`${BASE}/uploads/multipart/abort`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ id }),
  }).then(
    () => undefined,
    () => undefined
  );
}

// Local-backend path: multipart POST through the API, via XHR for real progress.
function uploadMultipart(
  file: File,
  onProgress?: (fraction: number) => void
): Promise<Dataset> {
  const form = new FormData();
  form.append("file", file);
  return new Promise<Dataset>((resolve, reject) => {
    const xhr = new XMLHttpRequest();
    xhr.open("POST", `${BASE}/uploads`);
    xhr.responseType = "json";
    xhr.upload.onprogress = (e) => {
      if (onProgress && e.lengthComputable) {
        onProgress(Math.min(0.95, e.loaded / e.total));
      }
    };
    xhr.onload = () => {
      const body = xhr.response as (Dataset & { detail?: string }) | null;
      if (xhr.status >= 200 && xhr.status < 300 && body) {
        onProgress?.(1);
        resolve(body);
      } else {
        reject(new Error(body?.detail || `Request failed (${xhr.status})`));
      }
    };
    xhr.onerror = () => reject(new Error("Network error during upload."));
    xhr.send(form);
  });
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
