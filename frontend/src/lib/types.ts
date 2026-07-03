import type { ColumnType } from "./api-types";

const EMAIL = /^[^@\s]+@[^@\s]+\.[^@\s]+$/;
const URLISH = /^https?:\/\/|^www\./i;
const NUMISH = /^-?\$?\d[\d,]*\.?\d*%?$/;
const DATEISH = /^\d{4}[-/]\d{1,2}[-/]\d{1,2}|^\d{1,2}[-/]\d{1,2}[-/]\d{2,4}/;

// Lightweight column-type inference (Airtable-style header glyphs) from the
// sampled preview values — purely cosmetic, never affects processing.
export function inferType(
  name: string | null | undefined,
  samples: unknown[] | null | undefined
): ColumnType {
  const vals = (samples || [])
    .map((v) => (v === null || v === undefined ? "" : String(v).trim()))
    .filter(Boolean)
    .slice(0, 8);
  const lname = (name || "").toLowerCase();
  const all = (re: RegExp) => vals.length > 0 && vals.every((v) => re.test(v));

  if (lname.includes("email") || all(EMAIL)) return { glyph: "@", title: "Email" };
  if (lname.includes("url") || lname.includes("link") || all(URLISH))
    return { glyph: "↗", title: "URL" };
  if (
    lname.includes("date") ||
    lname.includes("time") ||
    lname.endsWith("_at") ||
    all(DATEISH)
  )
    return { glyph: "◷", title: "Date / time" };
  if (lname === "id" || lname.endsWith("_id") || lname.includes("phone"))
    return { glyph: "#", title: "Identifier / number" };
  if (all(NUMISH)) return { glyph: "#", title: "Number", align: "right" };
  return { glyph: "T", title: "Text" };
}
