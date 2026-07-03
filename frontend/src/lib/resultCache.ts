// Session-scoped LRU cache of result-page responses.
//
// A result is immutable once its job reaches SUCCESS and job ids are unique per
// run, so a fetched page never goes stale within a session — no TTL, no
// revalidation. Switching back to a run/page already viewed can therefore be
// served from memory instead of re-fetching. Bounded so deep-paging a large
// result can't grow memory without limit.
import type { ResultsResponse } from "./api-types";

// ~60 pages of 100 rows is a predictable ceiling (~10–15 MB worst case).
const MAX_ENTRIES = 60;

// Map keeps insertion order, so the first key is the least-recently-used. get()
// and set() re-insert the touched key to move it to the most-recent end.
const cache = new Map<string, ResultsResponse>();

/** Cache key for one (run, page, filter) view. */
export function keyFor(runId: string, page: number, affectedOnly: boolean): string {
  return `${runId}|${page}|${affectedOnly}`;
}

/** Return the cached response for `key`, marking it most-recently-used. */
export function get(key: string): ResultsResponse | undefined {
  const hit = cache.get(key);
  if (hit === undefined) return undefined;
  cache.delete(key);
  cache.set(key, hit); // move to most-recent end
  return hit;
}

/** Store `value` under `key`, evicting the oldest entry past the cap. */
export function set(key: string, value: ResultsResponse): void {
  cache.delete(key); // re-insert so an existing key moves to most-recent end
  cache.set(key, value);
  if (cache.size > MAX_ENTRIES) {
    const oldest = cache.keys().next().value;
    if (oldest !== undefined) cache.delete(oldest);
  }
}
