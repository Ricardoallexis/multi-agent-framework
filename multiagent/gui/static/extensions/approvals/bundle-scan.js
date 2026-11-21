// Copyright 2026 Ricardoallexis and contributors
// SPDX-License-Identifier: Apache-2.0
// Bundle scan approval cards (osv_query, bundle_load) and live scan status for «Aprobaciones» (SKL-T03).
// Pure view-model helpers are exported with no DOM or network dependency so Node tests can verify them directly.
import { html, useEffect, useRef, useState } from "../../vendor/htm-preact-standalone.module.js";
import { ApiError, apiGet, apiPost } from "../../js/api.js";
import { Help, helpFor } from "../../js/help.js";
import { errorText, fmtDate } from "../../js/text.js";
import { StatusChip } from "../../js/ui/status-chip.js";
import { submitApprovalDecision } from "./model.js";

export const BUNDLE_SCAN_KINDS = Object.freeze(["osv_query", "bundle_load"]);
export const SCAN_POLL_MS = 3000;
export const IDLE_SCAN_POLL_MS = 10000;
export const MAX_DISPLAY_FINDINGS = 10;
export const SCANNING_NOTICE = "Escaneando · unos 25 s por carpeta";
export const FALSE_POSITIVE_NOTE =
  "Sin LLM, SkillSpector busca palabras y patrones: muchos hallazgos altos son falsos positivos. Revisa los principales antes de decidir";

export const VERDICT_LABELS = Object.freeze({
  safe: { text: "Seguro", tone: "success", explanation: "SkillSpector no encontró riesgos bloqueantes y cubrió el 100 %." },
  caution: { text: "Con avisos", tone: "warning", explanation: "SkillSpector encontró avisos para revisar." },
  do_not_install: { text: "No instalar", tone: "danger", explanation: "SkillSpector recomienda no instalar este bundle." },
  not_analyzed: { text: "Sin analizar", tone: "warning", explanation: "No se analizó el 100 % del bundle; requiere confirmación." },
  running: { text: "Escaneando", tone: "active", pulse: true, explanation: SCANNING_NOTICE },
});

export const FINDING_LEVEL_LABELS = Object.freeze({
  critical: { text: "Crítico", rank: 0, tone: "danger" },
  high: { text: "Alto", rank: 1, tone: "danger" },
  medium: { text: "Medio", rank: 2, tone: "warning" },
  low: { text: "Bajo", rank: 3, tone: "info" },
  info: { text: "Informativo", rank: 4, tone: "muted" },
});

const REASON_MESSAGES = Object.freeze({
  osv_rejected: "No se permitió consultar api.osv.dev.",
  coverage_below_100: "La cobertura del análisis fue menor al 100 %.",
  coverage_unknown: "No se pudo determinar la cobertura del análisis.",
  verdict_not_understood: "No se entendió el veredicto devuelto por SkillSpector.",
  unsigned_interpreter: "El intérprete de Python de SkillSpector no tiene firma válida.",
  timeout: "El escaneo agotó el tiempo máximo.",
  output_truncated: "La salida del escaneo superó el límite permitido.",
  bundle_scan_off: "El escaneo de bundles está desactivado.",
  mock_mode: "Ejecución en modo Mock sin ruta de SkillSpector configurada.",
});

const scopeOf = (id) => String(id).replace(/[^A-Za-z0-9_-]+/g, "-");
const asError = (failure) => (failure instanceof ApiError ? failure : new ApiError({ message: String(failure) }));
const cleanText = (value) => (value === null || value === undefined ? "" : String(value).trim());

export const isBundleScanApproval = (item) =>
  Boolean(item && typeof item === "object" && BUNDLE_SCAN_KINDS.includes(item.kind));

export const scanStatusPath = (bundleName) =>
  "/gui/api/bundles/" + encodeURIComponent(cleanText(bundleName)) + "/scan";

export function extractBundleName(item, scan = null) {
  if (scan && typeof scan === "object" && cleanText(scan.bundle)) return cleanText(scan.bundle);
  if (item && typeof item === "object") {
    if (item.destination && typeof item.destination === "object" && cleanText(item.destination.bundle)) {
      return cleanText(item.destination.bundle);
    }
    if (item.location && typeof item.location === "object" && cleanText(item.location.bundle)) {
      return cleanText(item.location.bundle);
    }
  }
  return "";
}

export function parseOsvLocation(location) {
  const host = "api.osv.dev";
  const raw = typeof location === "string" ? location.trim() : "";
  const prefix = "api.osv.dev receives package names and versions from:";
  let detail = raw;
  if (raw.toLowerCase().startsWith(prefix)) {
    detail = raw.slice(prefix.length).trim();
  }
  const hasNone = !detail || detail.toLowerCase() === "no manifests found";
  const manifests = hasNone
    ? []
    : detail.split(",").map((part) => part.trim()).filter(Boolean);
  const manifestsText = manifests.length > 0
    ? manifests.join(", ")
    : "No se encontraron manifiestos en la carpeta";
  const outboundSummary = manifests.length > 0
    ? "Nombres y versiones de los paquetes de los manifiestos: " + manifests.join(", ")
    : "Nombres y versiones de los paquetes (no se encontraron manifiestos declarados en la carpeta)";
  return { host, manifests, manifestsText, outboundSummary };
}

export function describeOsvQuery(item, scan = null) {
  const row = item && typeof item === "object" ? item : {};
  const bundle = extractBundleName(row, scan) || "Bundle sin nombre";
  const osv = parseOsvLocation(row.location);
  return {
    id: cleanText(row.id),
    kind: "osv_query",
    title: "Consulta a api.osv.dev",
    bundle,
    destinationHost: osv.host,
    manifests: osv.manifests,
    manifestsText: osv.manifestsText,
    outboundSummary: osv.outboundSummary,
    rejectionConsequence: "Si no se permite, el bundle queda «sin analizar».",
    fingerprint: cleanText(row.sha256 || (scan && scan.fingerprint)),
    createdAt: typeof row.created_at === "string" ? row.created_at : "",
    approveLabel: "Permitir la consulta",
    rejectLabel: "No permitir",
  };
}

export function formatCoverage(coverage) {
  if (!coverage || typeof coverage !== "object" || Array.isArray(coverage)) {
    return { entries: [], hasEntries: false, allComplete: false };
  }
  const entries = Object.entries(coverage).map(([rawFolder, rawPercent]) => {
    const folder = cleanText(rawFolder) || ".";
    if (typeof rawPercent === "number" && Number.isFinite(rawPercent)) {
      if (rawPercent < 0) {
        return { folder, percent: null, unanalyzed: true, text: "sin analizar" };
      }
      const percent = Math.round(rawPercent * 10) / 10;
      const unanalyzed = percent < 100;
      return {
        folder,
        percent,
        unanalyzed,
        text: unanalyzed ? percent + " % · sin analizar" : percent + " %",
      };
    }
    return { folder, percent: null, unanalyzed: true, text: "sin analizar" };
  });
  const hasEntries = entries.length > 0;
  const allComplete = hasEntries && entries.every((entry) => !entry.unanalyzed);
  return { entries, hasEntries, allComplete };
}

export function formatVerdict(scan) {
  if (!scan || typeof scan !== "object") {
    return { key: "not_analyzed", ...VERDICT_LABELS.not_analyzed };
  }
  if (scan.state === "running") {
    return { key: "running", ...VERDICT_LABELS.running };
  }
  const coverage = formatCoverage(scan.coverage);
  const raw = cleanText(scan.verdict || scan.state || "not_analyzed").toLowerCase().replace(/[\s-]+/g, "_");
  if (raw === "safe" || raw === "caution" || raw === "do_not_install") {
    if (raw !== "do_not_install" && coverage.hasEntries && !coverage.allComplete) {
      return { key: "not_analyzed", ...VERDICT_LABELS.not_analyzed };
    }
    return { key: raw, ...VERDICT_LABELS[raw] };
  }
  return { key: "not_analyzed", ...VERDICT_LABELS.not_analyzed };
}

export function selectTopFindings(findings, limit = MAX_DISPLAY_FINDINGS) {
  const list = Array.isArray(findings) ? findings : [];
  const normalized = [];
  list.forEach((raw, index) => {
    if (!raw || typeof raw !== "object") return;
    const rawLevel = cleanText(raw.level || raw.severity).toLowerCase();
    const levelInfo = FINDING_LEVEL_LABELS[rawLevel] || {
      text: rawLevel || "Sin nivel",
      rank: 99,
      tone: "muted",
    };
    const rule = cleanText(raw.rule || raw.rule_id || raw.id || raw.check) || "Regla sin nombre";
    const file = cleanText(raw.file || raw.path || raw.filename);
    const rawLine = raw.line !== undefined ? raw.line : raw.line_number;
    const line = typeof rawLine === "number" && Number.isInteger(rawLine) ? rawLine : null;
    const fileText = file ? (line !== null ? file + ":" + line : file) : (line !== null ? "línea " + line : "Archivo no indicado");
    const collapsed = cleanText(raw.message || raw.description || raw.title).replace(/\s+/g, " ");
    const summary = collapsed.length > 200 ? collapsed.slice(0, 199) + "…" : collapsed;
    normalized.push({
      index,
      levelKey: rawLevel || "unknown",
      level: levelInfo.text,
      tone: levelInfo.tone,
      rank: levelInfo.rank,
      rule,
      file,
      line,
      fileText,
      summary,
    });
  });
  normalized.sort((a, b) => (a.rank - b.rank) || (a.index - b.index));
  const cap = Number.isFinite(limit) && limit >= 0 ? Math.floor(limit) : MAX_DISPLAY_FINDINGS;
  const items = normalized.slice(0, cap).map(({ index, rank, ...rest }) => rest);
  return {
    items,
    total: normalized.length,
    hidden: Math.max(0, normalized.length - items.length),
  };
}

export function formatScanReason(reason) {
  const key = cleanText(reason);
  if (!key) return "";
  return REASON_MESSAGES[key] || "Motivo: " + key;
}

export function describeBundleLoad(item, scan = null) {
  const row = item && typeof item === "object" ? item : {};
  const bundle = extractBundleName(row, scan) || "Bundle sin nombre";
  return {
    id: cleanText(row.id),
    kind: "bundle_load",
    title: "Veredicto de SkillSpector",
    bundle,
    verdict: formatVerdict(scan),
    recommendationText: scan && (scan.recommendation === "do_not_install" || scan.state === "do_not_install")
      ? "Recomendación de SkillSpector: no instalar" : "",
    reasonText: formatScanReason(scan && scan.reason),
    coverage: formatCoverage(scan && scan.coverage),
    findings: selectTopFindings(scan && scan.findings),
    note: FALSE_POSITIVE_NOTE,
    toolVersion: cleanText(scan && scan.tool_version),
    fingerprint: cleanText(row.sha256 || (scan && scan.fingerprint)),
    createdAt: typeof row.created_at === "string" ? row.created_at : "",
    approveLabel: "Cargar este bundle",
    rejectLabel: "No cargar",
  };
}

export const canApproveBundleLoad = (riskAck, busy = false, sessionUnavailable = false) =>
  riskAck === true && !busy && !sessionUnavailable;

export const isScanningState = (scan) => Boolean(scan && typeof scan === "object" && scan.state === "running");
export const shouldPollScans = (scans) => Array.isArray(scans) && scans.some(isScanningState);

function CardError({ cardError, busy, sessionUnavailable, scope, onRetry }) {
  if (!cardError) return null;
  return html`<div class="approvals-card-error" role="alert">
    <p class="message error">${cardError.message}</p>
    ${cardError.retryable && html`<div class="approvals-retry">
      <button type="button" class="btn secondary approvals-btn" disabled=${busy || sessionUnavailable}
        ...${helpFor("approvals.retry_code", { scope })} onClick=${() => onRetry(cardError.decision)}>Intentar de nuevo</button>
      <${Help} topic="approvals.retry_code" compact=${true} scope=${scope} />
    </div>`}
  </div>`;
}

function OsvQueryCard({ item, token, selected, sessionUnavailable, onSessionUnavailable, onDecided, onStale }) {
  const [busy, setBusy] = useState(false);
  const [cardError, setCardError] = useState(null);
  const view = describeOsvQuery(item);
  const scope = scopeOf(view.id || "osv");
  const titleId = "approvals-card-" + scope + "-title";

  async function decide(decision) {
    if (busy || sessionUnavailable) return;
    setBusy(true);
    setCardError(null);
    const outcome = await submitApprovalDecision({
      item,
      decision,
      post: (path, body) => apiPost(path, token, body),
    });
    setBusy(false);
    if (outcome.ok) {
      try {
        window.dispatchEvent(new CustomEvent("multiagent:approval-decided", {
          detail: { id: item.id, kind: item.kind, bundle: view.bundle, decision: decision === "approve" ? "approved" : "rejected" },
        }));
      } catch (_) { /* ignore */ }
      onDecided(item.id, decision);
      return;
    }
    if (outcome.sessionUnavailable) onSessionUnavailable();
    setCardError({ message: outcome.message, retryable: Boolean(outcome.retryable), decision });
    if (outcome.stale) onStale();
  }

  return html`<li class=${"approvals-card" + (selected ? " approvals-card-selected" : "")} aria-labelledby=${titleId}>
    <div class="approvals-card-head">
      <p id=${titleId} class="approvals-card-title"><strong>${view.title}</strong></p>
      <time class="approvals-time muted small" dateTime=${view.createdAt || null}>${fmtDate(view.createdAt)}</time>
    </div>
    <p class="approvals-reason">Antes de analizar el bundle, SkillSpector necesita consultar vulnerabilidades de paquetes en internet.</p>
    <dl class="approvals-facts">
      <dt>Bundle</dt><dd>${view.bundle}</dd>
      <dt>A dónde sale</dt><dd><code>${view.destinationHost}</code></dd>
      <dt>Qué saldrá</dt><dd>${view.outboundSummary}</dd>
      <dt>Si no permites</dt><dd>${view.rejectionConsequence}</dd>
    </dl>
    <${CardError} cardError=${cardError} busy=${busy} sessionUnavailable=${sessionUnavailable} scope=${scope} onRetry=${decide} />
    <div class="approvals-actions" role="group" aria-label=${"Decisión para " + view.title}>
      <span class="approvals-action">
        <button type="button" class="btn approvals-btn" disabled=${busy || sessionUnavailable}
          ...${helpFor("approvals.osv_allow", { scope })} onClick=${() => decide("approve")}>
          ${busy ? "Enviando…" : view.approveLabel}
        </button>
        <${Help} topic="approvals.osv_allow" compact=${true} scope=${scope} />
      </span>
      <span class="approvals-action">
        <button type="button" class="btn secondary approvals-btn" disabled=${busy || sessionUnavailable}
          ...${helpFor("approvals.osv_deny", { scope })} onClick=${() => decide("reject")}>
          ${view.rejectLabel}
        </button>
        <${Help} topic="approvals.osv_deny" compact=${true} scope=${scope} />
      </span>
    </div>
  </li>`;
}

function ScanDetails({ view }) {
  return html`<div class="approvals-scan-body">
    <div class="approvals-scan-verdict">
      <span class="approvals-scan-bundle">Bundle: <strong>${view.bundle}</strong></span>
      <${StatusChip} status=${view.verdict.key} labels=${VERDICT_LABELS} />
    </div>
    ${view.recommendationText && html`<p class="approvals-reason">${view.recommendationText}</p>`}
    ${view.verdict.key === "do_not_install" && !view.coverage.allComplete && html`<p class="approvals-reason">Cobertura parcial</p>`}
    ${view.reasonText && html`<p class="approvals-reason">${view.reasonText}</p>`}
    <div class="approvals-scan-section">
      <p class="approvals-scan-label">Cobertura</p>
      ${view.coverage.hasEntries
        ? html`<ul class="approvals-coverage">
            ${view.coverage.entries.map((entry) => html`<li key=${entry.folder} class="approvals-coverage-item">
              <span class="approvals-coverage-folder">${entry.folder}</span>
              <span class=${"approvals-coverage-value" + (entry.unanalyzed ? " approvals-coverage-unanalyzed" : "")}>${entry.text}</span>
            </li>`)}
          </ul>`
        : html`<p class="muted small approvals-scan-empty">Sin datos de cobertura (sin analizar).</p>`}
    </div>
    <div class="approvals-scan-section">
      <p class="approvals-scan-label">Hallazgos principales</p>
      ${view.findings.items.length > 0
        ? html`<ul class="approvals-findings">
            ${view.findings.items.map((finding, idx) => html`<li key=${idx + "-" + finding.rule} class="approvals-finding">
              <div class="approvals-finding-head">
                <span class=${"badge tone-" + finding.tone}>${finding.level}</span>
                <code class="approvals-finding-rule">${finding.rule}</code>
                <span class="approvals-finding-file muted small">${finding.fileText}</span>
              </div>
              ${finding.summary && html`<p class="approvals-finding-line">${finding.summary}</p>`}
            </li>`)}
          </ul>`
        : html`<p class="muted small approvals-scan-empty">Sin hallazgos registrados.</p>`}
      ${view.findings.hidden > 0 && html`<p class="muted small approvals-scan-more">
        Mostrando ${view.findings.items.length} de ${view.findings.total} hallazgos.
      </p>`}
    </div>
    <p class="notice check approvals-scan-note">${view.note}</p>
  </div>`;
}

function BundleLoadCard({ item, token, selected, sessionUnavailable, onSessionUnavailable, onDecided, onStale }) {
  const [busy, setBusy] = useState(false);
  const [riskAck, setRiskAck] = useState(false);
  const [cardError, setCardError] = useState(null);
  const [scan, setScan] = useState(null);
  const [scanLoading, setScanLoading] = useState(true);
  const [scanError, setScanError] = useState(null);
  const bundleName = extractBundleName(item);
  const scope = scopeOf(item.id || "bundle");
  const titleId = "approvals-card-" + scope + "-title";

  useEffect(() => { setRiskAck(false); }, [item.id, item.sha256]);

  useEffect(() => {
    if (!bundleName) {
      setScanLoading(false);
      return undefined;
    }
    let alive = true;
    setScanLoading(true);
    apiGet(scanStatusPath(bundleName), token)
      .then((data) => {
        if (!alive) return;
        setScan(data);
        setScanError(null);
      })
      .catch((failure) => {
        if (!alive) return;
        setScanError(asError(failure));
      })
      .finally(() => {
        if (alive) setScanLoading(false);
      });
    return () => { alive = false; };
  }, [bundleName, token, item.sha256]);

  const view = describeBundleLoad(item, scan);

  async function decide(decision) {
    if (busy || sessionUnavailable) return;
    if (decision === "approve" && !canApproveBundleLoad(riskAck, busy, sessionUnavailable)) return;
    setBusy(true);
    setCardError(null);
    const outcome = await submitApprovalDecision({
      item,
      decision,
      riskAck: decision === "approve" && riskAck,
      post: (path, body) => apiPost(path, token, body),
    });
    setBusy(false);
    if (outcome.ok || outcome.stale) setRiskAck(false);
    if (outcome.ok) {
      try {
        window.dispatchEvent(new CustomEvent("multiagent:approval-decided", {
          detail: { id: item.id, kind: item.kind, bundle: view.bundle, decision: decision === "approve" ? "approved" : "rejected" },
        }));
      } catch (_) { /* ignore */ }
      onDecided(item.id, decision);
      return;
    }
    if (outcome.sessionUnavailable) onSessionUnavailable();
    setCardError({ message: outcome.message, retryable: Boolean(outcome.retryable), decision });
    if (outcome.stale) onStale();
  }

  return html`<li class=${"approvals-card" + (selected ? " approvals-card-selected" : "")} aria-labelledby=${titleId}>
    <div class="approvals-card-head">
      <p id=${titleId} class="approvals-card-title"><strong>${view.title}</strong></p>
      <time class="approvals-time muted small" dateTime=${view.createdAt || null}>${fmtDate(view.createdAt)}</time>
    </div>
    ${scanLoading && html`<p class="muted small approvals-scan-loading">Leyendo el resultado del escaneo…</p>`}
    ${scanError && html`<p class="message error">No se pudo leer el estado del escaneo: ${errorText(scanError)}</p>`}
    <${ScanDetails} view=${view} />
    <${CardError} cardError=${cardError} busy=${busy} sessionUnavailable=${sessionUnavailable} scope=${scope} onRetry=${decide} />
    <div class="approvals-risk-ack">
      <label class="approvals-risk-label">
        <input type="checkbox" checked=${riskAck} disabled=${busy || sessionUnavailable}
          ...${helpFor("approvals.bundle_risk", { scope })}
          onChange=${(event) => setRiskAck(event.currentTarget.checked)} />
        <span>Asumo los riesgos de cargar este bundle</span>
      </label>
      <${Help} topic="approvals.bundle_risk" compact=${true} scope=${scope} />
    </div>
    <div class="approvals-actions" role="group" aria-label=${"Decisión para " + view.title}>
      <span class="approvals-action">
        <button type="button" class="btn approvals-btn" disabled=${!canApproveBundleLoad(riskAck, busy, sessionUnavailable)}
          ...${helpFor("approvals.bundle_allow", { scope })} onClick=${() => decide("approve")}>
          ${busy ? "Enviando…" : view.approveLabel}
        </button>
        <${Help} topic="approvals.bundle_allow" compact=${true} scope=${scope} />
      </span>
      <span class="approvals-action">
        <button type="button" class="btn secondary approvals-btn" disabled=${busy || sessionUnavailable}
          ...${helpFor("approvals.bundle_deny", { scope })} onClick=${() => decide("reject")}>
          ${view.rejectLabel}
        </button>
        <${Help} topic="approvals.bundle_deny" compact=${true} scope=${scope} />
      </span>
    </div>
  </li>`;
}

export function BundleScanCard(props) {
  if (props.item && props.item.kind === "osv_query") {
    return html`<${OsvQueryCard} ...${props} />`;
  }
  return html`<${BundleLoadCard} ...${props} />`;
}

export function BundleScanStatus({ token, items, onRefresh }) {
  const [scans, setScans] = useState([]);
  const wasRunning = useRef(false);
  const refreshRef = useRef(onRefresh);
  refreshRef.current = onRefresh;

  useEffect(() => {
    let alive = true;
    let timer = null;
    const active = shouldPollScans(scans);

    async function poll() {
      try {
        const bundles = await apiGet("/gui/api/bundles", token);
        if (!alive || !Array.isArray(bundles) || bundles.length === 0) {
          if (alive) setScans([]);
          return;
        }
        const results = await Promise.all(
          bundles
            .map((entry) => cleanText(entry && entry.name))
            .filter(Boolean)
            .map((name) => apiGet(scanStatusPath(name), token).catch(() => null)),
        );
        if (!alive) return;
        const external = results.filter((entry) => entry && entry.external);
        const runningNow = shouldPollScans(external);
        if (wasRunning.current && !runningNow && typeof refreshRef.current === "function") {
          refreshRef.current();
        }
        wasRunning.current = runningNow;
        setScans(external);
      } catch (_) {
        /* ignore background scan polling failures */
      }
    }

    poll();
    const intervalMs = active ? SCAN_POLL_MS : IDLE_SCAN_POLL_MS;
    timer = setInterval(() => {
      if (typeof document === "undefined" || !document.hidden) poll();
    }, intervalMs);

    const onDecided = () => poll();
    if (typeof window !== "undefined") {
      window.addEventListener("multiagent:approval-decided", onDecided);
    }
    return () => {
      alive = false;
      clearInterval(timer);
      if (typeof window !== "undefined") {
        window.removeEventListener("multiagent:approval-decided", onDecided);
      }
    };
  }, [token, shouldPollScans(scans)]);

  const pendingBundles = new Set(
    (Array.isArray(items) ? items : [])
      .filter(isBundleScanApproval)
      .map((item) => extractBundleName(item))
      .filter(Boolean),
  );
  const runningScans = scans.filter((scan) => isScanningState(scan));
  const cautionScans = scans.filter(
    (scan) => scan.state === "caution" && !pendingBundles.has(cleanText(scan.bundle))
      && Array.isArray(scan.findings) && scan.findings.length > 0,
  );

  if (runningScans.length === 0 && cautionScans.length === 0) return null;

  return html`<ul class="approvals-list approvals-scan-live help-always">
    ${runningScans.map((scan) => html`<li key=${"running-" + scan.bundle} class="approvals-card approvals-scan-running" role="status" aria-live="polite">
      <div class="approvals-card-head">
        <p class="approvals-card-title"><strong>Escaneo de bundle</strong></p>
        <${StatusChip} status="running" labels=${VERDICT_LABELS} />
      </div>
      <p class="approvals-reason">Bundle: <strong>${scan.bundle}</strong></p>
      <p class="approvals-scan-progress">${SCANNING_NOTICE}</p>
    </li>`)}
    ${cautionScans.map((scan) => {
      const view = describeBundleLoad({ id: "caution-" + scan.bundle }, scan);
      return html`<li key=${"caution-" + scan.bundle} class="approvals-card">
        <div class="approvals-card-head">
          <p class="approvals-card-title"><strong>${view.title}</strong></p>
        </div>
        <${ScanDetails} view=${view} />
      </li>`;
    })}
  </ul>`;
}
