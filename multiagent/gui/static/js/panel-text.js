// Copyright 2026 Ricardoallexis and contributors
// SPDX-License-Identifier: Apache-2.0
import { t, currentLanguage } from "./i18n.js";
// Texts and formatters for the Changes, Tokens, and Sources panels (GUI2-T05). These functions use no DOM or network.
// The server sends facts; wording comes from the UI dictionary. Unknown numbers are never shown as zero.

const known = (value) => typeof value === "number" && Number.isFinite(value);
const plural = (count, one, many) => count + " " + (count === 1 ? one : many);

export const UNKNOWN = "unknown";

export const fmtInt = (value) => value.toLocaleString(currentLanguage(), { maximumFractionDigits: 0 });

// Money: at least two decimals and up to six, because one call often costs a fraction of a cent.
export function fmtMoney(amount, currency) {
  return amount.toLocaleString(currentLanguage(), { minimumFractionDigits: 2, maximumFractionDigits: 6 }) + " " + (currency || "USD");
}

// --- paths ---------------------------------------------------------------------------------------------------------

const slashes = (path) => String(path).replace(/\\/g, "/");

// The path relative to the session folder when it lies inside it, else the path as it came.
export function relativePath(path, folder) {
  const text = String(path);
  if (!folder) return text;
  const base = slashes(folder).replace(/\/+$/, "");
  const full = slashes(text);
  if (base && full.length > base.length && full.slice(0, base.length).toLowerCase() === base.toLowerCase() && full[base.length] === "/") {
    return full.slice(base.length + 1);
  }
  return text;
}

// {dir, name}: the folder part (with its separator) and the file name, so the name can be shown in bold.
export function splitPath(path) {
  const text = String(path);
  const cut = Math.max(text.lastIndexOf("/"), text.lastIndexOf("\\"));
  return cut < 0 ? { dir: "", name: text } : { dir: text.slice(0, cut + 1), name: text.slice(cut + 1) || text };
}

// --- Cambios -------------------------------------------------------------------------------------------------------

export const editsText = (count) => plural(count, t("panel.edit_one"), t("panel.edit_many"));

// "3 files changed · +12 −4 lines", or what is missing when the source does not count lines.
export function changesSummary(totals) {
  if (!totals.files) return t("panel.no_changed_files");
  const files = plural(totals.files, t("panel.file_changed_one"), t("panel.files_changed_many"));
  if (totals.counted === 0) return files + t("panel.lines_unreported");
  const lines = "+" + fmtInt(totals.additions) + " −" + fmtInt(totals.deletions) + t("panel.lines");
  return files + " · " + lines + (totals.counted < totals.files ? t("panel.counted_prefix") + plural(totals.counted, t("panel.file_one"), t("panel.files_many")) + t("panel.counted_suffix") : "");
}

// --- Tokens --------------------------------------------------------------------------------------------------------

// «entrada 1.200 · salida 300», plus the cache when asked; a total alone when the source gives nothing finer; null is unknown.
export function tokensLine(sum, { cache = false } = {}) {
  if (!sum) return t("panel.unknown");
  const parts = [];
  if (known(sum.input)) parts.push(t("panel.input") + fmtInt(sum.input));
  if (known(sum.output)) parts.push(t("panel.output") + fmtInt(sum.output));
  if (cache && known(sum.cache_read)) parts.push(t("panel.cache_read") + fmtInt(sum.cache_read));
  if (cache && known(sum.cache_write)) parts.push(t("panel.cache_write") + fmtInt(sum.cache_write));
  if (known(sum.total) && !known(sum.input) && !known(sum.output)) parts.push(t("panel.total") + fmtInt(sum.total));
  return parts.length ? parts.join(" · ") : t("panel.unknown");
}

export const withoutData = (count) => (count > 0 ? count + t("panel.without_tokens") : "");

// The estimated cost of a run: unknown when no call has a price; partial when only
// some do; any amount in another currency is added apart (currencies are never summed together).
export function costText(cost) {
  if (!cost || cost.state === "unknown") return t("panel.unknown");
  const parts = [];
  if (known(cost.amount)) parts.push(fmtMoney(cost.amount, cost.currency));
  for (const extra of cost.other || []) parts.push(fmtMoney(extra.amount, extra.currency));
  const text = parts.join(" + ");
  return cost.state === "partial" ? text + t("panel.partial") + plural(cost.unknownCalls, t("panel.unpriced_one"), t("panel.unpriced_many")) + ")" : text;
}

// One sentence under the cost that says what it is, or why it is unknown.
export function costNote(cost) {
  if (!cost || cost.state === "unknown") {
    if (cost && cost.reason === "app") {
      return t("panel.app_cost_note");
    }
    if (cost && cost.reason === "none") return t("panel.no_model_calls");
    return t("panel.no_current_price");
  }
  if (cost.state === "partial") {
    return t("panel.partial_prefix") + plural(cost.unknownCalls, t("panel.call_one"), t("panel.calls_many")) +
      t("panel.partial_suffix");
  }
  return t("panel.cost_note");
}

export const appearsText = (count) => t("panel.appears") + plural(count, t("panel.result_one"), t("panel.results_many"));

// One side of a price: "0.15 USD per 1,000,000 tokens".
export function priceText(side, currency) {
  if (!side || !known(side.amount) || !known(side.denominator)) return t("panel.unknown");
  const unit = String(side.unit || "token").toLowerCase();
  const unitText = unit === "token" || unit === "tokens" ? "tokens" : unit;
  return side.amount.toLocaleString(currentLanguage(), { maximumFractionDigits: 8 }) + " " + (currency || "USD") + t("panel.price_per") + fmtInt(side.denominator) + " " + unitText;
}

const day = (value) => String(value || "").slice(0, 10) || "?";
const clip = (value, limit) => {
  const text = String(value || "");
  return text.length > limit ? text.slice(0, limit - 1) + "…" : text;
};

// "source: …; valid from 2026-01-01 to 2036-01-01; provenance: official_price_page"
export function evidenceText(side) {
  if (!side) return t("panel.no_evidence");
  return t("panel.source") + (clip(side.source, 140) || t("panel.not_reported")) + t("panel.valid_from") + day(side.valid_from) + t("panel.valid_until") + day(side.valid_until) +
    t("panel.provenance") + (side.provenance || t("panel.not_reported"));
}

// --- Fuentes -------------------------------------------------------------------------------------------------------

export const readsText = (count) => (count > 1 ? count + t("panel.reads_many") : t("panel.read_one"));
export const searchesText = (count) => plural(count, t("panel.search_one"), t("panel.search_many"));
export const attachmentsText = (count) => plural(count, t("panel.attach_one"), t("panel.attach_many"));

export function promptText(prompt) {
  return prompt.id + (prompt.version === null ? "" : " v" + prompt.version);
}

// --- reading state ---------------------------------------------------------------------------------------------------

export const loadingText = (loaded) => t("panel.reading") + (loaded ? " " + fmtInt(loaded) + t("panel.items") : "");
export const cappedText = (loaded) => t("panel.capped_prefix") + fmtInt(loaded) + t("panel.capped_suffix");
