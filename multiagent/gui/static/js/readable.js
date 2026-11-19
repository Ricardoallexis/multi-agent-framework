// Copyright 2026 Ricardoallexis and contributors
// SPDX-License-Identifier: Apache-2.0
// Pure, bounded description of a step output. The caller renders every value as text.

export const MAX_DEPTH = 3;
export const LONG_TEXT = 240;

export function readable(value, depth = 0) {
  if (value === null || value === undefined) return { kind: "text", text: "—" };
  if (depth >= MAX_DEPTH && typeof value === "object") {
    return { kind: "text", text: Array.isArray(value) ? "[Lista]" : "{Objeto}" };
  }
  if (Array.isArray(value)) {
    if (value.length && value.every((item) => item && typeof item === "object" && !Array.isArray(item))) {
      const columns = [...new Set(value.flatMap((item) => Object.keys(item)))];
      return { kind: "table", columns, rows: value.map((item) => columns.map((column) => readable(item[column], depth + 1))) };
    }
    return { kind: "list", items: value.map((item) => readable(item, depth + 1)) };
  }
  if (typeof value === "object") {
    return { kind: "fields", fields: Object.entries(value).map(([name, item]) => ({ name, value: readable(item, depth + 1) })) };
  }
  const text = String(value);
  return { kind: text.length > LONG_TEXT || text.includes("\n") ? "long" : "text", text };
}
