// Copyright 2026 Ricardoallexis and contributors
// SPDX-License-Identifier: Apache-2.0
// The Codex ACP card of «Apps y agentes» (ACP-T04): the step that is missing (install, probe), the consent of an area and the start of a
// conversation. The product never runs the install command: the panel shows it and the person runs it in their own console.
import "./i18n.js";
import { html, useEffect, useState } from "../../vendor/htm-preact-standalone.module.js";
import { ApiError, apiGet, apiPost, apiPut } from "../../js/api.js";
import { Help, helpFor } from "../../js/help.js";
import { acpStartHref } from "../../js/route.js";
import { t } from "../../js/i18n.js";
import { ACP_DELETE_ALL_PATH, ACP_ID, ACP_LIST_PATH, ACP_PROBE_PATH, ACP_CONSENT_PATH, NEW_SESSION_HREF, acpDeletePath, consentBody,
  consentFailureText, consentNote, consentReadPath, consentStateText, consentView, historyBody, historyRetentionText, historyRows,
  historyStateText, notReadyFailure, officeChoices, rowsOfBundle } from "./acp-model.js";

const asError = (failure) => (failure instanceof ApiError ? failure : new ApiError({ message: String(failure) }));

// «Comprobar» and «Comprobar otra vez» are the same probe: initialize only, never activation. It answers with the updated row.
function useProbe({ token, onRowUpdated, onChanged }) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState(null);
  async function probe() {
    if (busy) return;
    setBusy(true);
    setError(null);
    try {
      onRowUpdated(await apiPost(ACP_PROBE_PATH, token, {}));
      onChanged(ACP_ID);
    } catch (failure) {
      setError(asError(failure));
    } finally {
      setBusy(false);
    }
  }
  return { busy, error, probe };
}

function InstallPanel({ install, probe }) {
  const [open, setOpen] = useState(false);
  const [copied, setCopied] = useState("");
  async function copy() {
    try {
      await navigator.clipboard.writeText(install.command);
      setCopied("ok");
    } catch (_) {
      setCopied("failed");
    }
  }
  return html`<div class="apps-step apps-acp-install">
    <p class="apps-step-title">${t("ext.apps.acp.install.title")}</p>
    <p class="apps-step-text">${t("ext.apps.acp.install.detail")}</p>
    ${!open && html`<div class="apps-actions">
      <button type="button" class="btn" ...${helpFor("apps.acp_install")} onClick=${() => setOpen(true)}>${t("ext.apps.acp.install.button")}</button>
      <${Help} topic="apps.acp_install" compact=${true} />
    </div>`}
    ${open && html`<div class="apps-confirm apps-acp-install-panel" role="group" aria-label=${t("ext.apps.acp.install.title")}>
      <p class="apps-step-text">${t("ext.apps.acp.install.you_run_it")}</p>
      <p class="apps-snippet-label">${t("ext.apps.acp.install.command")}</p>
      <pre class="data">${install.command}</pre>
      <div class="apps-actions">
        <button type="button" class="btn secondary small" ...${helpFor("apps.copy", { scope: "acp" })} onClick=${copy}>${t("ext.apps.copy")}</button>
        <${Help} topic="apps.copy" compact=${true} scope="acp" />
        <span class="apps-status-line muted" role="status" aria-live="polite">
          ${copied === "ok" ? t("ext.apps.copy.copied") : copied === "failed" ? t("ext.apps.copy.failed") : ""}
        </span>
      </div>
      <dl class="apps-facts apps-acp-install-facts">
        ${install.version && html`<dt>${t("ext.apps.acp.install.version")}</dt><dd>${install.version}</dd>`}
        ${install.license && html`<dt>${t("ext.apps.acp.install.license")}</dt><dd>${install.license}</dd>`}
        ${install.source && html`<dt>${t("ext.apps.acp.install.source")}</dt><dd>${install.sourceHref
          ? html`<a href=${install.sourceHref}>${install.source}</a>` : install.source}</dd>`}
      </dl>
      <div class="apps-actions">
        <button type="button" class="btn" disabled=${probe.busy} ...${helpFor("apps.acp_probe")}
          onClick=${probe.probe}>${probe.busy ? t("ext.apps.acp.probing") : t("ext.apps.acp.check_again")}</button>
        <${Help} topic="apps.acp_probe" compact=${true} />
      </div>
    </div>`}
  </div>`;
}

function ProbeStep({ probe }) {
  return html`<div class="apps-step apps-acp-probe">
    <p class="apps-step-title">${t("ext.apps.acp.probe.title")}</p>
    <p class="apps-step-text">${t("ext.apps.acp.probe.detail")}</p>
    <div class="apps-actions">
      <button type="button" class="btn" disabled=${probe.busy} ...${helpFor("apps.acp_probe")}
        onClick=${probe.probe}>${probe.busy ? t("ext.apps.acp.probing") : t("ext.apps.acp.probe.button")}</button>
      <${Help} topic="apps.acp_probe" compact=${true} />
    </div>
  </div>`;
}

// The offices and areas the GUI already knows (the same two routes the sidebar reads), as one list of packages.
function useAreas(token, enabled) {
  const [state, setState] = useState({ groups: [], loading: false, error: null });
  useEffect(() => {
    if (!enabled) return undefined;
    let active = true;
    setState((current) => ({ ...current, loading: true, error: null }));
    (async () => {
      try {
        const list = await apiGet("/gui/api/offices", token);
        const offices = Array.isArray(list && list.offices) ? list.offices : [];
        const details = await Promise.all(offices.filter((office) => office && typeof office.id === "string")
          .map((office) => apiGet("/gui/api/offices/" + encodeURIComponent(office.id), token).catch(() => null)));
        if (active) setState({ groups: officeChoices(details.filter(Boolean)), loading: false, error: null });
      } catch (failure) {
        if (active) setState({ groups: [], loading: false, error: asError(failure) });
      }
    })();
    return () => { active = false; };
  }, [token, enabled]);
  return state;
}

// The saved conversations: read when the card opens and again after each deletion, never on a timer.
function useHistory(token) {
  const [state, setState] = useState({ rows: [], loading: true, error: null });
  const [round, setRound] = useState(0);
  useEffect(() => {
    let active = true;
    setState((current) => ({ ...current, loading: true, error: null }));
    apiGet(ACP_LIST_PATH, token).then(
      (payload) => { if (active) setState({ rows: historyRows(payload), loading: false, error: null }); },
      (failure) => { if (active) setState({ rows: [], loading: false, error: asError(failure) }); },
    );
    return () => { active = false; };
  }, [token, round]);
  return { ...state, reload: () => setRound((value) => value + 1) };
}

// A conversation that is already gone (404) is a deletion that happened: only another failure stops the loop.
async function deleteConversations(token, ids) {
  for (const id of ids) {
    try { await apiPost(acpDeletePath(id), token, {}); } catch (failure) {
      if (!(failure instanceof ApiError && failure.status === 404)) throw failure;
    }
  }
}

function ConsentBox({ token, bundle, history }) {
  const [consent, setConsent] = useState(null);       // null while it loads
  const [problem, setProblem] = useState("");
  const [editing, setEditing] = useState(false);
  const [allowWrite, setAllowWrite] = useState(false);
  const [busy, setBusy] = useState(false);
  const [saved, setSaved] = useState(false);
  const [offer, setOffer] = useState(false);          // after the history switch was turned off: offer to delete what was saved
  const [removed, setRemoved] = useState(false);

  useEffect(() => {
    let active = true;
    setConsent(null); setProblem(""); setEditing(false); setSaved(false); setOffer(false); setRemoved(false);
    if (!bundle) return undefined;
    apiGet(consentReadPath(bundle), token).then(
      (payload) => { if (active) { const view = consentView(payload); setConsent(view); setAllowWrite(view.allowWrite); } },
      (failure) => { if (active) setProblem(consentFailureText(asError(failure))); },
    );
    return () => { active = false; };
  }, [bundle, token]);

  async function save() {
    if (busy) return;
    setBusy(true); setProblem(""); setSaved(false);
    try {
      const view = consentView(await apiPut(ACP_CONSENT_PATH, token, consentBody(bundle, allowWrite)));
      setConsent(view); setAllowWrite(view.allowWrite); setEditing(false); setSaved(true);
    } catch (failure) {
      setProblem(consentFailureText(asError(failure)));
    } finally {
      setBusy(false);
    }
  }

  // The switch only changes `keep_history`; the write permission goes along as it is. It needs a saved consent: a PUT of an area that
  // never had one would create it, and a consent grants reading, so the switch waits for the person to set it up first.
  async function saveHistory(next) {
    if (busy || !consent || !consent.configured) return;
    setBusy(true); setProblem(""); setSaved(false); setRemoved(false);
    try {
      const view = consentView(await apiPut(ACP_CONSENT_PATH, token, historyBody(bundle, consent.allowWrite, next)));
      setConsent(view); setAllowWrite(view.allowWrite); setOffer(!view.keepHistory);
      history.reload();
    } catch (failure) {
      setProblem(consentFailureText(asError(failure)));
    } finally {
      setBusy(false);
    }
  }
  const ofArea = rowsOfBundle(history.rows, bundle);
  async function deleteArea() {   // only the person presses it: one deletion by id for each saved conversation of this area
    if (busy) return;
    setBusy(true); setProblem("");
    try {
      await deleteConversations(token, ofArea.map((row) => row.id));
      setOffer(false); setRemoved(true);
    } catch (failure) {
      setProblem(t("ext.apps.acp.history.delete_failed", { message: asError(failure).message }));
    } finally {
      setBusy(false);
      history.reload();
    }
  }

  return html`<div class="apps-acp-consent">
    <p class="apps-step-title">${t("ext.apps.acp.consent.title")}<${Help} topic="apps.acp_consent" compact=${true} /></p>
    <p class="apps-notice">${consentNote()}</p>
    ${bundle && consent === null && !problem && html`<p class="muted" role="status" aria-busy="true">${t("ext.apps.acp.consent.loading")}</p>`}
    ${consent && html`<p class="apps-step-text apps-acp-consent-state" role="status">${consentStateText(consent)}</p>`}
    ${consent && !editing && html`<div class="apps-actions">
      <button type="button" class="btn secondary" ...${helpFor("apps.acp_consent")}
        onClick=${() => { setSaved(false); setEditing(true); }}>${consent.configured ? t("ext.apps.acp.consent.change") : t("ext.apps.acp.consent.configure")}</button>
    </div>`}
    ${consent && editing && html`<div class="apps-confirm" role="group" aria-label=${t("ext.apps.acp.consent.title")}>
      <div class="apps-acp-check">
        <button type="button" class="apps-acp-switch" role="switch" aria-checked=${allowWrite ? "true" : "false"} disabled=${busy}
          ...${helpFor("apps.acp_write")} onClick=${() => setAllowWrite(!allowWrite)}>
          <span class="apps-acp-switch-track" aria-hidden="true"><span class="apps-acp-switch-thumb"></span></span>
          <span>${t("ext.apps.acp.consent.allow_write")}</span>
        </button>
        <${Help} topic="apps.acp_write" compact=${true} />
      </div>
      <div class="apps-actions">
        <button type="button" class="btn" disabled=${busy} ...${helpFor("apps.acp_consent")}
          onClick=${save}>${busy ? t("ext.apps.acp.consent.saving") : t("ext.apps.acp.consent.save")}</button>
        <button type="button" class="btn secondary" disabled=${busy} data-help-exempt="confirm"
          onClick=${() => setEditing(false)}>${t("ext.apps.cancel")}</button>
      </div>
    </div>`}
    ${consent && html`<div class="apps-acp-history-setting">
      <div class="apps-acp-check">
        <button type="button" class="apps-acp-switch" role="switch" aria-checked=${consent.keepHistory ? "true" : "false"}
          disabled=${busy || !consent.configured} ...${helpFor("apps.acp_keep_history")} onClick=${() => saveHistory(!consent.keepHistory)}>
          <span class="apps-acp-switch-track" aria-hidden="true"><span class="apps-acp-switch-thumb"></span></span>
          <span>${t("ext.apps.acp.history.switch")}</span>
        </button>
        <${Help} topic="apps.acp_keep_history" compact=${true} />
      </div>
      <p class="apps-step-text apps-acp-history-state" role="status">${historyStateText(consent)} ${historyRetentionText(consent)}</p>
      ${!consent.configured && html`<p class="muted">${t("ext.apps.acp.history.needs_consent")}</p>`}
    </div>`}
    ${offer && ofArea.length > 0 && html`<div class="apps-confirm" role="group" aria-label=${t("ext.apps.acp.history.title")}>
      <p class="apps-step-text">${t("ext.apps.acp.history.offer", { count: String(ofArea.length) })}</p>
      <div class="apps-actions">
        <button type="button" class="btn" disabled=${busy} ...${helpFor("apps.acp_offer_delete")}
          onClick=${deleteArea}>${busy ? t("ext.apps.acp.history.deleting") : t("ext.apps.acp.history.offer_delete")}</button>
        <${Help} topic="apps.acp_offer_delete" compact=${true} />
        <button type="button" class="btn secondary" disabled=${busy} data-help-exempt="confirm"
          onClick=${() => setOffer(false)}>${t("ext.apps.acp.history.offer_keep")}</button>
      </div>
    </div>`}
    ${removed && html`<p class="message ok apps-status-line" role="status">${t("ext.apps.acp.history.removed")}</p>`}
    ${saved && html`<p class="message ok apps-status-line" role="status">${t("ext.apps.acp.consent.saved")}</p>`}
    ${problem && html`<p class="message error apps-status-line" role="alert">${problem}</p>`}
  </div>`;
}

// The conversations this device saved (ACP-T05): open one to read it, or delete them all. Nothing here resumes the agent.
function HistorySection({ token, history }) {
  const [confirming, setConfirming] = useState(false);
  const [busy, setBusy] = useState(false);
  const [problem, setProblem] = useState("");
  const [removed, setRemoved] = useState(null);   // how many the last «delete all» removed
  async function deleteAll() {   // only the person presses it
    if (busy) return;
    setBusy(true); setProblem(""); setRemoved(null);
    try {
      const answer = await apiPost(ACP_DELETE_ALL_PATH, token, {});
      setRemoved(answer && Number.isInteger(answer.deleted) ? answer.deleted : 0);
      setConfirming(false);
    } catch (failure) {
      setProblem(t("ext.apps.acp.history.delete_failed", { message: asError(failure).message }));
    } finally {
      setBusy(false);
      history.reload();
    }
  }
  return html`<div class="apps-step apps-acp-history">
    <p class="apps-step-title">${t("ext.apps.acp.history.title")}<${Help} topic="apps.acp_history" compact=${true} /></p>
    ${history.loading && html`<p class="muted" role="status" aria-busy="true">${t("ext.apps.acp.history.loading")}</p>`}
    ${history.error && html`<p class="message error apps-status-line" role="alert">${t("ext.apps.acp.history.failed", { message: history.error.message })}</p>`}
    ${!history.loading && !history.error && history.rows.length === 0 && html`<p class="muted">${t("ext.apps.acp.history.none")}</p>`}
    ${history.rows.length > 0 && html`<ul class="apps-acp-history-list">
      ${history.rows.map((row) => html`<li key=${row.id} class="apps-acp-history-row">
        ${row.readable ? html`<a href=${row.href}>${row.label}${row.when ? " · " + row.when : ""}</a>` : html`<span>${t("ext.apps.acp.history.unreadable_file")}</span>`}
        <span class="muted apps-acp-history-meta">${row.readable ? t("ext.apps.acp.history.count", { count: String(row.count) }) : ""}${row.unreadable && row.readable
          ? " · " + t("ext.apps.acp.history.unreadable") : ""}${row.active ? " · " + t("ext.apps.acp.history.live") : ""}${row.redacted
          ? " · " + t("ext.apps.acp.history.redacted") : ""}</span>
      </li>`)}
    </ul>`}
    ${history.rows.length > 0 && !confirming && html`<div class="apps-actions">
      <button type="button" class="btn secondary" disabled=${busy} ...${helpFor("apps.acp_delete_all")}
        onClick=${() => { setRemoved(null); setConfirming(true); }}>${t("ext.apps.acp.history.delete_all")}</button>
      <${Help} topic="apps.acp_delete_all" compact=${true} />
    </div>`}
    ${confirming && html`<div class="apps-confirm" role="group" aria-label=${t("ext.apps.acp.history.delete_all")}>
      <p class="message error apps-status-line" role="alert">${t("ext.apps.acp.history.delete_all.confirm")}</p>
      <div class="apps-actions">
        <button type="button" class="btn" disabled=${busy} data-help-exempt="confirm"
          onClick=${deleteAll}>${busy ? t("ext.apps.acp.history.deleting") : t("ext.apps.acp.history.delete_all.yes")}</button>
        <button type="button" class="btn secondary" disabled=${busy} data-help-exempt="confirm"
          onClick=${() => setConfirming(false)}>${t("ext.apps.cancel")}</button>
      </div>
    </div>`}
    ${removed !== null && html`<p class="message ok apps-status-line" role="status">${t("ext.apps.acp.history.deleted", { count: String(removed) })}</p>`}
    ${problem && html`<p class="message error apps-status-line" role="alert">${problem}</p>`}
    <p class="apps-notice">${t("ext.apps.acp.history.note")}</p>
    <p class="apps-notice">${t("ext.apps.acp.history.redaction")}</p>
  </div>`;
}

// «Empezar conversación» only navigates: the first message is written in the conversation view, which carries the package.
function StartLink({ bundle, ready, probeNeeded }) {
  const failure = ready ? null : notReadyFailure(probeNeeded);
  return html`<div class="apps-acp-start">
    ${ready && html`<div class="apps-actions">
      <a class="btn" href=${acpStartHref(bundle)} ...${helpFor("apps.acp_start")}>${t("ext.apps.acp.start.button")}</a>
      <${Help} topic="apps.acp_start" compact=${true} />
    </div>`}
    ${failure && html`<div class="apps-acp-unavailable" role="alert">
      <p class="apps-step-title">${failure.title}</p>
      <p class="message error apps-status-line">${failure.text}</p>
      <div class="apps-actions">
        <a class="btn secondary" href=${NEW_SESSION_HREF} ...${helpFor("apps.acp_cli")}>${t("ext.apps.acp.choose_cli")}</a>
        <${Help} topic="apps.acp_cli" compact=${true} />
      </div>
    </div>`}
  </div>`;
}

// The area is chosen once for the consent and the conversation. Only an activated adapter shows them.
function AreaSection({ token, canStart, probeNeeded, history }) {
  const areas = useAreas(token, true);
  const [bundle, setBundle] = useState("");
  return html`<div class="apps-step apps-acp-area">
    <label for="apps-acp-area-select" class="apps-step-title">${t("ext.apps.acp.area.label")}</label>
    <select id="apps-acp-area-select" value=${bundle} disabled=${areas.loading} ...${helpFor("apps.acp_area")}
      onChange=${(event) => setBundle(event.target.value)}>
      <option value="">${areas.loading ? t("ext.apps.acp.area.loading") : t("ext.apps.acp.area.choose")}</option>
      ${areas.groups.map((group) => html`<optgroup key=${group.id} label=${group.name}>
        ${group.areas.map((area) => html`<option key=${area.bundle} value=${area.bundle}>${group.name} / ${area.name}</option>`)}
      </optgroup>`)}
    </select>
    <${Help} topic="apps.acp_area" compact=${true} />
    ${areas.error && html`<p class="message error apps-status-line" role="alert">${t("ext.apps.acp.area.failed", { message: areas.error.message })}</p>`}
    ${!areas.loading && !areas.error && areas.groups.length === 0 && html`<p class="muted">${t("ext.apps.acp.area.none")}</p>`}
    ${bundle && html`<${ConsentBox} token=${token} bundle=${bundle} history=${history} />`}
    ${bundle && html`<${StartLink} bundle=${bundle} ready=${canStart} probeNeeded=${probeNeeded} />`}
  </div>`;
}

// The extra part of the Codex ACP card: the step that is missing comes first; consent and conversation only when activated.
export function AcpPanel({ item, token, onRowUpdated, onChanged }) {
  const acp = item.acp;
  const probe = useProbe({ token, onRowUpdated, onChanged });
  const history = useHistory(token);
  const installable = acp.step === "install" && item.action && item.action.kind === "acp_install" && acp.install;
  return html`<div class="apps-acp-panel">
    ${installable && html`<${InstallPanel} install=${acp.install} probe=${probe} />`}
    ${acp.step === "probe" && html`<${ProbeStep} probe=${probe} />`}
    ${probe.error && html`<p class="message error apps-status-line" role="alert">${probe.error.message}</p>`}
    ${acp.activated && html`<${AreaSection} token=${token} canStart=${acp.canStart} probeNeeded=${acp.step === "probe"} history=${history} />`}
    <${HistorySection} token=${token} history=${history} />
  </div>`;
}
