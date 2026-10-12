// Copyright 2026 Ricardoallexis and contributors
// SPDX-License-Identifier: Apache-2.0
// Texts and formatters of the right-panel sections «Cambios», «Tokens» and «Fuentes» (GUI2-T05), in Spanish. Pure
// functions (no DOM, no Preact, no imports) so they can be checked on their own. The server sends facts; the wording
// lives here. A number that is not known is never written as 0: it reads «desconocido».

const known = (value) => typeof value === "number" && Number.isFinite(value);
const plural = (count, one, many) => count + " " + (count === 1 ? one : many);

export const UNKNOWN = "desconocido";

export const fmtInt = (value) => value.toLocaleString("es", { maximumFractionDigits: 0 });

// Money: at least two decimals and up to six, because one call often costs a fraction of a cent.
export function fmtMoney(amount, currency) {
  return amount.toLocaleString("es", { minimumFractionDigits: 2, maximumFractionDigits: 6 }) + " " + (currency || "USD");
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

export const editsText = (count) => plural(count, "edición", "ediciones");

// «3 archivos modificados · +12 −4 líneas», or what is missing when the source does not count lines.
export function changesSummary(totals) {
  if (!totals.files) return "Ningún archivo modificado.";
  const files = plural(totals.files, "archivo modificado", "archivos modificados");
  if (totals.counted === 0) return files + " · líneas añadidas y quitadas: no constan";
  const lines = "+" + fmtInt(totals.additions) + " −" + fmtInt(totals.deletions) + " líneas";
  return files + " · " + lines + (totals.counted < totals.files ? " (de " + plural(totals.counted, "archivo", "archivos") + " con recuento)" : "");
}

// --- Tokens --------------------------------------------------------------------------------------------------------

// «entrada 1.200 · salida 300», plus the cache when asked; a total alone when the source gives nothing finer; null is unknown.
export function tokensLine(sum, { cache = false } = {}) {
  if (!sum) return UNKNOWN;
  const parts = [];
  if (known(sum.input)) parts.push("entrada " + fmtInt(sum.input));
  if (known(sum.output)) parts.push("salida " + fmtInt(sum.output));
  if (cache && known(sum.cache_read)) parts.push("caché leída " + fmtInt(sum.cache_read));
  if (cache && known(sum.cache_write)) parts.push("caché escrita " + fmtInt(sum.cache_write));
  if (known(sum.total) && !known(sum.input) && !known(sum.output)) parts.push("total " + fmtInt(sum.total));
  return parts.length ? parts.join(" · ") : UNKNOWN;
}

export const withoutData = (count) => (count > 0 ? count + " sin datos de tokens" : "");

// The estimated cost of a run: «desconocido» when no call has a price; «… (parcial: 2 llamadas sin precio)» when only
// some do; any amount in another currency is added apart (currencies are never summed together).
export function costText(cost) {
  if (!cost || cost.state === "unknown") return UNKNOWN;
  const parts = [];
  if (known(cost.amount)) parts.push(fmtMoney(cost.amount, cost.currency));
  for (const extra of cost.other || []) parts.push(fmtMoney(extra.amount, extra.currency));
  const text = parts.join(" + ");
  return cost.state === "partial" ? text + " (parcial: " + plural(cost.unknownCalls, "llamada sin precio", "llamadas sin precio") + ")" : text;
}

// One sentence under the cost that says what it is, or why it is unknown.
export function costNote(cost) {
  if (!cost || cost.state === "unknown") {
    if (cost && cost.reason === "app") {
      return "Las apps no informan el precio de cada llamada, así que no se estima el costo. Solo las ejecuciones del framework lo estiman, con la evidencia de precio del catálogo.";
    }
    if (cost && cost.reason === "none") return "Todavía no hay llamadas a modelos en esta ejecución.";
    return "Ninguna llamada tiene un precio vigente en el catálogo con tokens declarados, y un costo sin evidencia no se inventa.";
  }
  if (cost.state === "partial") {
    return "Estimación: tokens × precio del catálogo. La suma deja fuera " + plural(cost.unknownCalls, "llamada", "llamadas") +
      " sin precio vigente o sin tokens declarados.";
  }
  return "Estimación: tokens × precio del catálogo vigente en cada llamada; no es un importe facturado.";
}

export const appearsText = (count) => "en " + plural(count, "resultado", "resultados");

// One side of a price: «0,15 USD por 1.000.000 tokens».
export function priceText(side, currency) {
  if (!side || !known(side.amount) || !known(side.denominator)) return UNKNOWN;
  const unit = String(side.unit || "token").toLowerCase();
  const unitText = unit === "token" || unit === "tokens" ? "tokens" : unit;
  return side.amount.toLocaleString("es", { maximumFractionDigits: 8 }) + " " + (currency || "USD") + " por " + fmtInt(side.denominator) + " " + unitText;
}

const day = (value) => String(value || "").slice(0, 10) || "?";
const clip = (value, limit) => {
  const text = String(value || "");
  return text.length > limit ? text.slice(0, limit - 1) + "…" : text;
};

// «fuente: …; vigente del 2026-01-01 al 2036-01-01; procedencia: official_price_page»
export function evidenceText(side) {
  if (!side) return "sin evidencia";
  return "fuente: " + (clip(side.source, 140) || "sin indicar") + "; vigente del " + day(side.valid_from) + " al " + day(side.valid_until) +
    "; procedencia: " + (side.provenance || "sin indicar");
}

// --- Fuentes -------------------------------------------------------------------------------------------------------

export const readsText = (count) => (count > 1 ? count + " lecturas" : "1 lectura");
export const searchesText = (count) => plural(count, "búsqueda web", "búsquedas web");
export const attachmentsText = (count) => plural(count, "adjunto (imagen o documento)", "adjuntos (imágenes o documentos)");

export function promptText(prompt) {
  return prompt.id + (prompt.version === null ? "" : " v" + prompt.version);
}

// --- reading state ---------------------------------------------------------------------------------------------------

export const loadingText = (loaded) => "Leyendo la sesión…" + (loaded ? " " + fmtInt(loaded) + " elementos" : "");
export const cappedText = (loaded) => "Solo se analizaron los primeros " + fmtInt(loaded) + " elementos de la sesión.";
