// Recently-opened files, tracked client-side in localStorage — no auth, no
// server-side ownership. Every UploadedFile is addressable by id
// (`GET /uploads/<id>`), so remembering the ids here is enough to reopen any
// file the user has touched on this browser. Opening a new file no longer
// discards the previous one; it just moves to the front of this list.

import type { Dataset } from "./api-types";

export interface RecentFile {
  id: string;
  original_name: string;
  kind: string;
  size_bytes: number;
  columns: number;
  opened_at: string; // ISO — feeds timeAgo()
}

const KEY = "ds.recent";
const MAX = 12;

export function getRecents(): RecentFile[] {
  try {
    const list = JSON.parse(localStorage.getItem(KEY) || "[]");
    return Array.isArray(list) ? (list as RecentFile[]) : [];
  } catch {
    return [];
  }
}

function save(list: RecentFile[]): void {
  try {
    localStorage.setItem(KEY, JSON.stringify(list.slice(0, MAX)));
  } catch {
    /* storage full / unavailable — recents are best-effort */
  }
}

/** Record (or bump to the front) a file the user just opened. */
export function pushRecent(ds: Dataset): void {
  const entry: RecentFile = {
    id: ds.id,
    original_name: ds.original_name,
    kind: ds.kind,
    size_bytes: ds.size_bytes,
    columns: ds.columns?.length ?? 0,
    opened_at: new Date().toISOString(),
  };
  save([entry, ...getRecents().filter((r) => r.id !== ds.id)]);
}

/** Forget a file (user dismissed it, or the server no longer has it). */
export function removeRecent(id: string): void {
  save(getRecents().filter((r) => r.id !== id));
}
