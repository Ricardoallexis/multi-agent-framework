// Copyright 2026 Ricardoallexis and contributors
// SPDX-License-Identifier: Apache-2.0
// «Add an agent» of «Apps y agentes» (EXA-T06): the person declares a program that speaks ACP and sees the agents they declared. The
// form exists only in advanced mode. Only the person's press adds, checks, activates, edits or removes: opening the screen reads the
// list and nothing else, and nothing here polls. Every text from the server or from the agent is drawn as text by the template.
import "./i18n.js";
import { html, useEffect, useRef, useState } from "../../vendor/htm-preact-standalone.module.js";
import { ApiError, apiGet, apiPost, apiPut } from "../../js/api.js";
import { Help, helpFor } from "../../js/help.js";
import { acpStartHref } from "../../js/route.js";
import { t } from "../../js/i18n.js";
import { StatusChip } from "../../js/ui/status-chip.js";
import { activationNote, rememberExecutorName } from "./activation-model.js";
import { activateExecutor, deactivateExecutor, requestActivation } from "./activation.js";
import { useAreas, useProbe } from "./acp.js";
import { AGENTS_PATH, CUSTOMIZE_HREF, addBody, advancedOnlyText, agentPath, agentRows, canSave, editBody, editNotice, emptyForm, failureText,
  fixedNote, formOf, isAgentGone, removeConfirmText, replaceAgent } from "./add-agent-model.js";

const asError = (failure) => (failure instanceof ApiError ? failure : new ApiError({ message: String(failure) }));
const postWith = (token) => (path, body) => apiPost(path, token, body);

const stateLabels = () => ({
  disabled: { text: t("ext.apps.agents.state.disabled"), tone: "neutral", shape: "ring", explanation: t("ext.apps.agents.chip.disabled") },
  active: { text: t("ext.apps.agents.state.active"), tone: "success", shape: "disc", explanation: t("ext.apps.agents.chip.active") },
  paused: { text: t("ext.apps.agents.state.paused"), tone: "warning", shape: "diamond", explanation: t("ext.apps.agents.chip.paused") },
  invalid: { text: t("ext.apps.agents.state.invalid"), tone: "danger", shape: "square", explanation: t("ext.apps.agents.chip.invalid") },
});

// The list is read when the section mounts and after each action, never on a timer.
function useAgents(token) {
  const [state, setState] = useState({ agents: [], loading: true, error: null });
  const [round, setRound] = useState(0);
  useEffect(() => {
    let active = true;
    apiGet(AGENTS_PATH, token).then(
      (payload) => {
        if (!active) return;
        const agents = agentRows(payload);
        agents.forEach((agent) => rememberExecutorName(agent.id, agent.name));
        setState({ agents, loading: false, error: null });
      },
      (failure) => { if (active) setState({ agents: [], loading: false, error: asError(failure) }); },
    );
    return () => { active = false; };
  }, [token, round]);
  return {
    ...state,
    reload: () => setRound((value) => value + 1),
    replace: (row) => {
      rememberExecutorName(row && row.id, row && row.name);
      setState((current) => ({ ...current, agents: replaceAgent(current.agents, row) }));
    },
  };
}

// The same form adds and edits. `notice` is what saving will do (edit only); the fixed note is always there.
export function AgentForm({ scope, form, setForm, busy, error, onSubmit, onCancel, editing, notice }) {
  const field = (name) => (event) => setForm((current) => ({ ...current, [name]: event.target.value }));
  const id = "apps-agent-" + scope;
  return html`<form class="apps-agent-form" onSubmit=${(event) => { event.preventDefault(); if (!busy && canSave(form)) onSubmit(); }}>
    <div class="apps-agent-field">
      <label for=${id + "-name"}>${t("ext.apps.agents.field.name")}</label>
      <input id=${id + "-name"} type="text" maxlength="60" autocomplete="off" value=${form.name} disabled=${busy}
        ...${helpFor("apps.agent_name", { scope })} onInput=${field("name")} />
      <${Help} topic="apps.agent_name" compact=${true} scope=${scope} />
    </div>
    <div class="apps-agent-field">
      <label for=${id + "-program"}>${t("ext.apps.agents.field.program")}</label>
      <input id=${id + "-program"} type="text" autocomplete="off" spellcheck="false" value=${form.executable} disabled=${busy}
        ...${helpFor("apps.agent_program", { scope })} onInput=${field("executable")} />
      <${Help} topic="apps.agent_program" compact=${true} scope=${scope} />
    </div>
    <div class="apps-agent-field">
      <label for=${id + "-args"}>${t("ext.apps.agents.field.args")}</label>
      <textarea id=${id + "-args"} rows="4" autocomplete="off" spellcheck="false" value=${form.args} disabled=${busy}
        ...${helpFor("apps.agent_args", { scope })} onInput=${field("args")}></textarea>
      <${Help} topic="apps.agent_args" compact=${true} scope=${scope} />
    </div>
    ${notice && notice.text && html`<p class=${"apps-notice apps-agent-edit-notice apps-agent-edit-" + notice.kind} role="status">${notice.text}</p>`}
    <p class="apps-notice apps-agent-note">${fixedNote()}</p>
    <div class="apps-actions">
      ${editing ? html`<button type="submit" class="btn" disabled=${busy || !canSave(form)} ...${helpFor("apps.agent_save", { scope })}>${busy ? t("ext.apps.agents.saving") : t("ext.apps.agents.save")}</button>
        <${Help} topic="apps.agent_save" compact=${true} scope=${scope} />`
        : html`<button type="submit" class="btn" disabled=${busy || !canSave(form)} ...${helpFor("apps.agent_add", { scope })}>${busy ? t("ext.apps.agents.adding") : t("ext.apps.agents.add")}</button>
        <${Help} topic="apps.agent_add" compact=${true} scope=${scope} />`}
      ${onCancel && html`<button type="button" class="btn secondary" disabled=${busy} data-help-exempt="confirm"
        onClick=${onCancel}>${t("ext.apps.cancel")}</button>`}
    </div>
    ${error && html`<p class="message error apps-status-line" role="alert">${failureText(error)}</p>`}
  </form>`;
}

// «Start a conversation»: the person picks the area (the same two reads as the Codex ACP card, only once they open it) and the link
// opens the thread of ACP with this agent. Nothing is sent from here.
function StartPicker({ agentId, token }) {
  const areas = useAreas(token, true);
  const [bundle, setBundle] = useState("");
  return html`<div class="apps-step apps-agent-start">
    <label for=${"apps-agent-area-" + agentId} class="apps-step-title">${t("ext.apps.acp.area.label")}</label>
    <select id=${"apps-agent-area-" + agentId} value=${bundle} disabled=${areas.loading} ...${helpFor("apps.agent_area", { scope: agentId })}
      onChange=${(event) => setBundle(event.target.value)}>
      <option value="">${areas.loading ? t("ext.apps.acp.area.loading") : t("ext.apps.acp.area.choose")}</option>
      ${areas.groups.map((group) => html`<optgroup key=${group.id} label=${group.name}>
        ${group.areas.map((area) => html`<option key=${area.bundle} value=${area.bundle}>${group.name} / ${area.name}</option>`)}
      </optgroup>`)}
    </select>
    <${Help} topic="apps.agent_area" compact=${true} scope=${agentId} />
    ${areas.error && html`<p class="message error apps-status-line" role="alert">${t("ext.apps.acp.area.failed", { message: areas.error.message })}</p>`}
    ${!areas.loading && !areas.error && areas.groups.length === 0 && html`<p class="muted">${t("ext.apps.acp.area.none")}</p>`}
    ${bundle && html`<div class="apps-actions">
      <a class="btn" ...${helpFor("apps.agent_start", { scope: agentId })} href=${acpStartHref(bundle, agentId)}>${t("ext.apps.agents.start.go")}</a>
      <${Help} topic="apps.agent_start" compact=${true} scope=${agentId} />
    </div>`}
  </div>`;
}

function AgentCard({ agent, token, onChanged, onRowUpdated }) {
  const [panel, setPanel] = useState("idle");   // idle | edit | remove | note | start
  const [busy, setBusy] = useState("");          // requesting | activating | deactivating | saving | removing
  const [message, setMessage] = useState(null);  // {ok, text}
  const [problem, setProblem] = useState(null);  // the failure of the form or of the removal
  const [form, setForm] = useState(() => formOf(agent));
  const [note, setNote] = useState(null);
  const [request, setRequest] = useState(null);
  const confirmRef = useRef(null);
  const probe = useProbe({ token, onRowUpdated, onChanged: () => {}, path: agentPath(agent.id, "probe"), id: agent.id });
  const can = (action) => agent.actions.includes(action);
  const working = busy !== "" || probe.busy;

  useEffect(() => {
    if (panel === "note" && confirmRef.current) confirmRef.current.focus();
  }, [panel]);

  // The agent was removed somewhere else: the list is read again and says so.
  function settle(failure) {
    if (isAgentGone(failure)) onChanged();
  }

  async function openNote() {
    if (working) return;
    setMessage(null);
    setBusy("requesting");
    const outcome = await requestActivation({ executorId: agent.id, post: postWith(token) });
    setBusy("");
    if (!outcome.ok) {
      setMessage({ ok: false, text: outcome.message });
      if (outcome.refresh || outcome.restart) onChanged();
      return;
    }
    setRequest(outcome.request);
    setNote(activationNote(outcome.request, agent.id));
    setPanel("note");
  }

  function cancelNote() {
    setNote(null);
    setRequest(null);
    setPanel("idle");
  }

  // The button of the note is the person's approval; the conversation, the list and this page never approve.
  async function accept() {
    if (panel !== "note" || working) return;
    setBusy("activating");
    const outcome = await activateExecutor({ token, executorId: agent.id, post: postWith(token), request });
    setBusy("");
    cancelNote();
    setMessage({ ok: outcome.ok, text: outcome.message });
    if (outcome.ok || outcome.refresh || outcome.restart) onChanged();
  }

  async function turnOff() {
    if (working) return;
    setMessage(null);
    setBusy("deactivating");
    const outcome = await deactivateExecutor({ token, executorId: agent.id, post: postWith(token) });
    setBusy("");
    setMessage({ ok: outcome.ok, text: outcome.message });
    if (outcome.ok || outcome.refresh) onChanged();
  }

  async function save() {
    if (working) return;
    const body = editBody(agent, form);
    if (Object.keys(body).length === 0) { setPanel("idle"); return; }
    setBusy("saving");
    setProblem(null);
    try {
      onRowUpdated(await apiPut(agentPath(agent.id), token, body));
      setPanel("idle");
      setMessage({ ok: true, text: t("ext.apps.agents.saved") });
    } catch (failure) {
      setProblem(asError(failure));
      settle(failure);
    } finally {
      setBusy("");
    }
  }

  async function remove() {
    if (working) return;
    setBusy("removing");
    setProblem(null);
    try {
      await apiPost(agentPath(agent.id, "remove"), token, {});
      onChanged();
    } catch (failure) {
      setProblem(asError(failure));
      settle(failure);
      setPanel("idle");
    } finally {
      setBusy("");
    }
  }

  function openEdit() {
    setForm(formOf(agent));
    setProblem(null);
    setMessage(null);
    setPanel("edit");
  }

  const scope = agent.id;
  return html`<li class="apps-card apps-agent-card">
    <div class="apps-card-body">
      <p class="apps-agent-head">
        <strong class="apps-card-name apps-agent-name">${agent.name}</strong>
        <${StatusChip} status=${agent.state} labels=${stateLabels()} variant="inline" />
      </p>
      <dl class="apps-facts">
        <dt>${t("ext.apps.agents.fact.program")}</dt><dd class="apps-agent-program"><code>${agent.executable}</code></dd>
        <dt>${t("ext.apps.agents.fact.args")}</dt>
        <dd>${agent.args.length === 0 ? t("ext.apps.agents.fact.args_none") : html`<ol class="apps-agent-args">
          ${agent.args.map((arg, index) => html`<li key=${index}><code>${arg}</code></li>`)}
        </ol>`}</dd>
        ${agent.facts.map((fact) => html`<dt key=${"t" + fact.id}>${fact.label}</dt><dd key=${"d" + fact.id}>${fact.value}</dd>`)}
      </dl>
      <p class="apps-step-text muted apps-agent-scope">${agent.scopeLine}</p>
      ${agent.lines.map((line) => html`<p key=${line.id} class=${"apps-agent-line " + (line.tone === "error" ? "message error apps-status-line" : "apps-notice")}
        role=${line.tone === "error" ? "alert" : "status"}>${line.text}</p>`)}
      ${panel === "idle" && html`<div class="apps-actions apps-agent-actions">
        ${can("check") && html`<button type="button" class="btn secondary" disabled=${working} ...${helpFor("apps.agent_check", { scope })}
          onClick=${probe.probe}>${probe.busy ? t("ext.apps.acp.probing") : t("ext.apps.acp.probe.button")}</button>
        <${Help} topic="apps.agent_check" compact=${true} scope=${scope} />`}
        ${can("activate") && html`<button type="button" class="btn" disabled=${working} ...${helpFor("apps.activate", { scope })}
          onClick=${openNote}>${busy === "requesting" ? t("ext.apps.activation.preparing")
            : agent.activateReview ? t("ext.apps.activation.review_activate") : t("ext.apps.activation.activate")}</button>
        <${Help} topic="apps.activate" compact=${true} scope=${scope} />`}
        ${can("deactivate") && html`<button type="button" class="btn secondary" disabled=${working} ...${helpFor("apps.deactivate", { scope })}
          onClick=${turnOff}>${busy === "deactivating" ? t("ext.apps.activation.deactivating") : t("ext.apps.activation.deactivate")}</button>
        <${Help} topic="apps.deactivate" compact=${true} scope=${scope} />`}
        ${agent.canStart && html`<button type="button" class="btn" disabled=${working} ...${helpFor("apps.agent_start", { scope })}
          onClick=${() => setPanel("start")}>${t("ext.apps.agents.start.button")}</button>
        <${Help} topic="apps.agent_start" compact=${true} scope=${scope} />`}
        ${can("edit") && html`<button type="button" class="btn secondary" disabled=${working} ...${helpFor("apps.agent_edit", { scope })}
          onClick=${openEdit}>${t("ext.apps.agents.edit")}</button>
        <${Help} topic="apps.agent_edit" compact=${true} scope=${scope} />`}
        ${can("remove") && html`<button type="button" class="btn secondary" disabled=${working} ...${helpFor("apps.agent_remove", { scope })}
          onClick=${() => { setProblem(null); setMessage(null); setPanel("remove"); }}>${t("ext.apps.agents.remove")}</button>
        <${Help} topic="apps.agent_remove" compact=${true} scope=${scope} />`}
      </div>`}
      ${panel === "note" && note && html`<div class="apps-confirm apps-activation-note" role="group" aria-label=${note.title}>
        <p class="apps-step-title">${note.title}<${Help} topic="apps.agent_note" compact=${true} scope=${scope} /></p>
        ${note.lines.map((line, index) => html`<p key=${index} class="apps-step-text">${line}</p>`)}
        <div class="apps-actions">
          <button type="button" class="btn" ref=${confirmRef} disabled=${working} data-help-exempt="confirm" onClick=${accept}>${note.confirmLabel}</button>
          <button type="button" class="btn secondary" disabled=${working} data-help-exempt="confirm" onClick=${cancelNote}>${t("ext.apps.cancel")}</button>
        </div>
      </div>`}
      ${panel === "edit" && html`<${AgentForm} scope=${"edit-" + scope} form=${form} setForm=${setForm} busy=${busy === "saving"} error=${problem}
        onSubmit=${save} onCancel=${() => { setProblem(null); setPanel("idle"); }} editing=${true} notice=${editNotice(agent, form)} />`}
      ${panel === "remove" && html`<div class="apps-confirm" role="group" aria-label=${t("ext.apps.agents.remove")}>
        <p class="message error apps-status-line" role="alert">${removeConfirmText()}</p>
        <div class="apps-actions">
          <button type="button" class="btn" disabled=${working} data-help-exempt="confirm"
            onClick=${remove}>${busy === "removing" ? t("ext.apps.agents.removing") : t("ext.apps.agents.remove.yes")}</button>
          <button type="button" class="btn secondary" disabled=${working} data-help-exempt="confirm"
            onClick=${() => setPanel("idle")}>${t("ext.apps.agents.remove.keep")}</button>
        </div>
      </div>`}
      ${panel === "start" && html`<${StartPicker} agentId=${agent.id} token=${token} />`}
      ${panel === "start" && html`<div class="apps-actions">
        <button type="button" class="btn secondary" data-help-exempt="confirm" onClick=${() => setPanel("idle")}>${t("ext.apps.cancel")}</button>
      </div>`}
      ${busy === "activating" && html`<p class="apps-status-line muted" role="status">${t("ext.apps.activation.working")}</p>`}
      ${message && html`<p class=${"message apps-status-line " + (message.ok ? "ok" : "error")} role=${message.ok ? "status" : "alert"}>${message.text}</p>`}
      ${probe.error && html`<p class="message error apps-status-line" role="alert">${failureText(probe.error)}</p>`}
      ${problem && panel !== "edit" && html`<p class="message error apps-status-line" role="alert">${failureText(problem)}</p>`}
    </div>
  </li>`;
}

function AddAgentForm({ token, onAdded }) {
  const [form, setForm] = useState(emptyForm);
  const [busy, setBusy] = useState(false);
  const [problem, setProblem] = useState(null);
  const [added, setAdded] = useState(false);
  async function add() {
    if (busy || !canSave(form)) return;
    setBusy(true);
    setProblem(null);
    setAdded(false);
    try {
      await apiPost(AGENTS_PATH, token, addBody(form));
      setForm(emptyForm());
      setAdded(true);
      onAdded();
    } catch (failure) {
      setProblem(asError(failure));
    } finally {
      setBusy(false);
    }
  }
  return html`<div class="apps-step apps-agent-add">
    <${AgentForm} scope="add" form=${form} setForm=${setForm} busy=${busy} error=${problem} onSubmit=${add} onCancel=${null} editing=${false} notice=${null} />
    ${added && html`<p class="message ok apps-status-line" role="status">${t("ext.apps.agents.added")}</p>`}
  </div>`;
}

function AdvancedAgents({ token }) {
  const agents = useAgents(token);
  return html`<div class="apps-agents-body">
    <${AddAgentForm} token=${token} onAdded=${agents.reload} />
    ${agents.loading && html`<p class="muted" role="status" aria-busy="true">${t("ext.apps.agents.loading")}</p>`}
    ${agents.error && html`<p class="message error apps-status-line" role="alert">${t("ext.apps.agents.list_failed", { message: failureText(agents.error) })}</p>`}
    ${!agents.loading && !agents.error && agents.agents.length === 0 && html`<p class="muted">${t("ext.apps.agents.none")}</p>`}
    ${agents.agents.length > 0 && html`<ul class="apps-grid apps-agent-list" aria-label=${t("ext.apps.agents.list_label")}>
      ${agents.agents.map((agent) => html`<${AgentCard} key=${agent.id} agent=${agent} token=${token} onChanged=${agents.reload}
        onRowUpdated=${agents.replace} />`)}
    </ul>`}
  </div>`;
}

// In simple mode there is no form: one line says it is an advanced option and links to «Personalizar».
export function AgentsSection({ mode, token }) {
  const advanced = mode !== "simple";
  return html`<section class="apps-agents" aria-labelledby="apps-agents-title">
    <h4 id="apps-agents-title" class="apps-agents-title">${t("ext.apps.agents.title")}<${Help} topic="apps.agents" compact=${true} /></h4>
    ${advanced ? html`<${AdvancedAgents} token=${token} />` : html`<p class="apps-step-text apps-agents-simple">${advancedOnlyText()}
      <a class="apps-agents-link" href=${CUSTOMIZE_HREF}>${t("ext.apps.agents.customize_link")}</a></p>`}
  </section>`;
}
