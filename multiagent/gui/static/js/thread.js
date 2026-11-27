// Copyright 2026 Ricardoallexis and contributors
// SPDX-License-Identifier: Apache-2.0
// The run conversation in Nueva sesión. Reads only while this view is mounted and visible.
import { html, useState, useEffect, useRef } from "../vendor/htm-preact-standalone.module.js";
import { ApiError, apiGet, apiPost, runPath } from "./api.js";
import { HumanActions } from "./human-actions.js";
import { Help, helpFor } from "./help.js";
import { errorText } from "./text.js";
import { AcpCard, ThreadCard } from "./thread-cards.js";
import { authHeaders } from "./auth.js";
import { parseBlock } from "./stream.js";
import { DocumentCard } from "./document-card.js";
import { SegmentBar } from "./segment-bar.js";   // AVZ-T04
import { acpStartHref, acpThreadHref } from "./route.js";
import { currentMode } from "./gui-mode.js";   // SIM-T03
import { POLL_MS, composerState, createThread, entryKey, groupThread, isNearEnd, isTerminal, mergeThread, pollAfter,
  nextFollowing, shouldFollow, SMOOTH_MS, tailKey, projectOf, shortcutsFor,
  ACP_LIST_PATH, ACP_START_PATH, acpBundleOf, acpConsentReadPath, acpDeletePath, acpHistoryWarningKey, acpStartBody, acpStartFailure,
  acpStartedId, acpComposerState, acpNeedsConsentNotice, acpPath, acpStreamPath, applyAcpStreamBlock, createAcpThread, groupAcpEvents,
  isAcpLost, mergeAcpPage, stepBar, waitSeqOf } from "./thread-model.js";
import { MESSAGE_KINDS, NEEDS_CONFIRMATION, messageError, messageId, messagePath } from "./inbox-model.js";
import { t } from "./i18n.js";

// EJC-T01: the nearest ancestor that scrolls (the page itself when none does).
function scrollerOf(node) {
  for (let parent = node && node.parentElement; parent; parent = parent.parentElement) {
    const overflow = getComputedStyle(parent).overflowY;
    if ((overflow === "auto" || overflow === "scroll") && parent.scrollHeight > parent.clientHeight) return parent;
  }
  return document.scrollingElement || document.documentElement;
}
const readPosition = (scroller) => ({ top: scroller.scrollTop, height: scroller.clientHeight, total: scroller.scrollHeight });

// GUI4-T14: the links to the views that hang from a conversation. The session header and the run thread draw this same row.
// Each shortcut is a real link (keyboard, middle click, Back). On a narrow screen the row folds into a «Más» button (CSS only
// shows one or the other; the links are never drawn twice).
export function Shortcuts({ mode, sessionId, workers = null, project = null, scope = "main" }) {
  const [open, setOpen] = useState(false);
  const listId = "shortcuts-list-" + scope;   // two headers mounted together must not repeat the id
  const found = shortcutsFor({ mode, sessionId, workers, project });
  if (!found.length) return null;
  const link = (id) => found.find((item) => item.id === id);
  const label = (item) => t(item.label, item.data);
  const [tree, projectLink, usage, map] = ["tree", "project", "usage", "map"].map(link);
  return html`<nav class="shortcuts" aria-label=${t("thread.shortcuts.aria")}>
    <div class="shortcuts-toggle">
      <button type="button" class="btn secondary small" aria-expanded=${open ? "true" : "false"}
        aria-controls=${listId} ...${helpFor("session.shortcuts_more")} onClick=${() => setOpen(!open)}>${t("thread.shortcuts.more")}</button>
      <${Help} topic="session.shortcuts_more" compact=${true} />
    </div>
    <div id=${listId} class=${"shortcuts-list" + (open ? " open" : "")}>
      ${tree && html`<a class="btn secondary small" href=${tree.href} ...${helpFor("agent_tree.open")}>${label(tree)}</a>
        <${Help} topic="agent_tree.open" compact=${true} />`}
      ${projectLink && html`<a class="btn secondary small" href=${projectLink.href} ...${helpFor("session.shortcut_project")}>${label(projectLink)}</a>
        <${Help} topic="session.shortcut_project" compact=${true} />`}
      ${usage && html`<a class="btn secondary small" href=${usage.href} ...${helpFor("session.shortcut_usage")}>${label(usage)}</a>
        <${Help} topic="session.shortcut_usage" compact=${true} />`}
      ${map && html`<a class="btn secondary small" href=${map.href} ...${helpFor("session.shortcut_map")}>${label(map)}</a>
        <${Help} topic="session.shortcut_map" compact=${true} />`}
    </div>
  </nav>`;
}

export function RunThread({ token, runId }) {
  const [thread, setThread] = useState(createThread);
  const endRef = useRef(null);
  const following = useRef(true);   // the person is at the end (or has not scrolled yet)
  const lastTail = useRef("");
  const guardUntil = useRef(0);   // programmatic smooth scroll in progress until this time
  const settle = useRef(null);
  const [error, setError] = useState(null);
  const [reviewView, setReviewView] = useState(null);
  const [runLimit, setRunLimit] = useState(null);
  const [decisions, setDecisions] = useState({});   // AVZ-T04: what this person decided in this thread, by step id
  const [runSteps, setRunSteps] = useState(null);   // AVZ-T04: the last list of steps the thread read (the run view, while it waits or fails)
  const [busy, setBusy] = useState(false);
  const current = useRef(createThread());
  const ticket = useRef(0);
  const inFlight = useRef(false);

  async function load(full = false) {
    if (document.hidden) return;
    if (inFlight.current && !full) return;
    inFlight.current = true;
    const request = ++ticket.current;
    try {
      const after = full ? 0 : pollAfter(current.current);
      const page = await apiGet("/gui/api/threads/runs/" + encodeURIComponent(runId) + "?after=" + after, token);
      let view = null;
      let reviewFailure = null;
      if (page.status === "waiting_human" || page.status === "failed") {
        try { view = await apiGet(runPath(runId), token); }
        catch (failure) { reviewFailure = failure; }
      }
      if (request !== ticket.current) return;
      current.current = mergeThread(full ? createThread() : current.current, page);
      setThread(current.current);
      setReviewView(page.status === "waiting_human" ? view : null);
      setRunLimit(view ? view.limit || null : null);
      if (view && Array.isArray(view.steps)) setRunSteps(view.steps);
      if (Array.isArray(page.steps)) setRunSteps(page.steps);   // AVZ-T05: the page's own list wins; it comes from the same read as the messages
      setError(reviewFailure);
    } catch (failure) {
      if (request === ticket.current) setError(failure);
    } finally {
      if (request === ticket.current) inFlight.current = false;
    }
  }

  useEffect(() => {
    current.current = createThread(); setThread(current.current); setReviewView(null); setRunLimit(null); setRunSteps(null); setDecisions({}); setError(null);
    load(true);
    const timer = setInterval(() => { if (!isTerminal(current.current.status)) load(); }, POLL_MS);
    const visible = () => { if (!document.hidden && !isTerminal(current.current.status)) load(); };
    document.addEventListener("visibilitychange", visible);
    return () => { ticket.current += 1; inFlight.current = false; clearInterval(timer); document.removeEventListener("visibilitychange", visible); };
  }, [runId, token]);

  // Remember whether the person is reading the end; a scroll anywhere in the page updates it.
  useEffect(() => {
    const onScroll = () => {
      if (endRef.current) following.current = nextFollowing(following.current, guardUntil.current, Date.now(), readPosition(scrollerOf(endRef.current)));
    };
    document.addEventListener("scroll", onScroll, true);
    return () => { document.removeEventListener("scroll", onScroll, true); clearTimeout(settle.current); };
  }, []);
  useEffect(() => { following.current = true; lastTail.current = ""; }, [runId]);
  // A new message brings the end into view only for a person who was already there; a reader above is not moved.
  useEffect(() => {
    const tail = tailKey(thread.entries);
    const before = lastTail.current;
    lastTail.current = tail;
    if (!before || !shouldFollow(following.current, before, tail) || !endRef.current) return;
    let reduced = false;
    try { reduced = window.matchMedia("(prefers-reduced-motion: reduce)").matches; } catch (_) { reduced = false; }
    guardUntil.current = Date.now() + (reduced ? 0 : SMOOTH_MS);
    endRef.current.scrollIntoView({ behavior: reduced ? "auto" : "smooth", block: "end" });
    clearTimeout(settle.current);   // once it ends, measure again
    settle.current = setTimeout(() => { if (endRef.current) following.current = isNearEnd(readPosition(scrollerOf(endRef.current))); }, SMOOTH_MS + 50);
  }, [thread.entries]);

  async function act(action, body, where = null) {
    if (busy) return;
    setBusy(true); setError(null);
    try {
      const stepId = (where && where.stepId) || reviewView?.waiting?.step_id || "";   // the card that decided, or the view of the wait
      const waitSeq = where && Number.isFinite(where.waitSeq) ? where.waitSeq : waitSeqOf(thread.entries, stepId);
      await apiPost(runPath(runId, action), token, body);
      if (stepId && waitSeq !== null && (action === "approve" || action === "changes")) {
        setDecisions((previous) => ({ ...previous, [stepId]: { wait_seq: waitSeq, choice: action } }));   // tied to that wait
      }
      await load(true);
    } catch (failure) {
      setError(failure);
      if (failure.status === 409 || failure.status === 404) await load(true);
    } finally { setBusy(false); }
  }

  // AVZ-T04: the card that decides says which wait it decides (its step and its seq), so the decision is tied to it
  const actFor = (entry) => (action, body) => act(action, body, { stepId: entry.step_id || entry.decision?.step_id || "", waitSeq: Number(entry.seq) });
  const bar = stepBar(runSteps, thread.entries, thread.status, decisions);
  return html`<section class="ns thread-view" aria-label=${t("thread.title")}>
    <div class="thread-head"><h3>${t("thread.title")}</h3><span class="muted">${runId}</span>
      <button type="button" class="btn secondary" disabled=${busy} ...${helpFor("thread.refresh")}
        onClick=${() => load(true)}>${t("thread.refresh")}</button><${Help} topic="thread.refresh" compact=${true} />
      <${Shortcuts} mode=${currentMode()} sessionId=${"framework:" + runId} project=${projectOf(reviewView) || projectOf(thread.entries.find((entry) => entry.kind === "launched"))} scope="run" /></div>
    <${Help} topic="thread.section" />
    ${bar && html`<div class="thread-progress">
      <${SegmentBar} segments=${bar.segments} summary=${bar.summary} className="thread-segbar" />
      <p class="thread-progress-line muted small">${bar.line}</p></div>`}
    ${error && html`<p class="message error" role="alert">${t("thread.refresh.error", { message: errorText(error) })}</p>`}
    ${!thread.loaded && !error && html`<p class="muted" aria-busy="true">${t("thread.loading")}</p>`}
    <ol class="thread-list" role="log" aria-label=${t("thread.messages.aria")}>
      ${groupThread(thread.entries, thread.status).map((item) => item.type === "entry"
        ? html`<${ThreadCard} key=${entryKey(item.entry)} entry=${item.entry} status=${thread.status}
          runId=${runId} token=${token} reviewView=${reviewView} busy=${busy} onAction=${actFor(item.entry)} limit=${runLimit} mode=${currentMode()} />`
        : html`<li key=${item.key} class="thread-fold"><details class="thread-earlier-reviews">
          <summary>${t("thread.earlier_reviews")} (${item.entries.length})</summary>
          <ol class="thread-list">${item.entries.map((entry) => html`<${ThreadCard} key=${entryKey(entry)} entry=${entry}
            status=${thread.status} runId=${runId} token=${token} reviewView=${null} busy=${busy} onAction=${actFor(entry)}
            mode=${currentMode()} />`)}</ol></details></li>`)}
    </ol>
    ${currentMode() === "simple" && reviewView?.waiting?.reason === "execute_step" && html`
      <${HumanActions} token=${token} view=${reviewView} onUpdate=${(updated) => { setReviewView(updated); load(true); }}
        onStale=${() => load(true)} />`}
    <div ref=${endRef} class="thread-end" aria-hidden="true"></div>
    ${thread.status === "completed" && html`<${DocumentCard} token=${token} runId=${runId} />`}
    <${RunInbox} runId=${runId} token=${token} status=${thread.status} onSent=${() => load(true)} />
  </section>`;
}

// AVA-T05: the message box belongs to the run thread and never calls a decision route.
const KIND_LABELS = {
  cancel: "thread.inbox.kind.cancel",
  interrupt: "thread.inbox.kind.interrupt",
  adjust: "thread.inbox.kind.adjust",
  note: "thread.inbox.kind.note",
};

function RunInbox({ runId, token, status, onSent }) {
  const [policy, setPolicy] = useState(null);       // EJC-T01: null while it loads; «disabled» and «failed to read» differ
  const [policyError, setPolicyError] = useState(null);
  const [kind, setKind] = useState("note");
  const [body, setBody] = useState("");
  const [confirming, setConfirming] = useState(false);
  const [busy, setBusy] = useState(false);
  const [notice, setNotice] = useState("");
  const draftId = useRef(null);

  useEffect(() => {
    let active = true;
    setKind("note"); setBody(""); setConfirming(false); setNotice(""); draftId.current = null;
    setPolicy(null); setPolicyError(null);
    apiGet("/gui/api/inbox/policy", token).then((value) => { if (active) setPolicy(value); },
      (error) => { if (active) setPolicyError(error); });
    return () => { active = false; };
  }, [token, runId]);

  const unavailable = isTerminal(status);
  const shown = composerState({ policy, error: policyError, status });
  const policyOn = Boolean(policy && policy.enabled);
  const reason = unavailable ? t("thread.inbox.policy.ended") : (policy && policy.reason) || "";
  const problem = messageError(kind, body, policyOn && !unavailable, reason);
  const changeKind = (event) => { setKind(event.target.value); setConfirming(false); draftId.current = null; };
  const changeBody = (event) => { setBody(event.target.value); setConfirming(false); draftId.current = null; };
  const send = async () => {
    if (busy || problem) return;
    setBusy(true); setNotice("");
    if (!draftId.current) draftId.current = messageId();
    try {
      await apiPost(messagePath("runs", runId), token, { message_id: draftId.current, kind, text: body.trim() });
      draftId.current = null; setBody(""); setConfirming(false); setNotice(t("thread.inbox.saved"));
      onSent();
    } catch (error) {
      setNotice(error?.details?.reason === "availability_disabled"
        ? t("thread.inbox.policy.disabled_server") : errorText(error));
      setConfirming(false);
    } finally { setBusy(false); }
  };
  const start = () => {
    if (problem || busy) return;
    if (NEEDS_CONFIRMATION.includes(kind)) setConfirming(true);
    else send();
  };

  if (shown === "hidden") return null;   // the message policy is off: nothing to write to
  if (shown === "error") return html`<section class="thread-inbox" aria-label=${t("thread.inbox.title")}>
    <p class="message error" role="alert">${t("thread.inbox.policy.error", { message: errorText(policyError) })}</p></section>`;
  if (shown === "loading") return html`<section class="thread-inbox" aria-label=${t("thread.inbox.title")}>
    <p class="muted" aria-busy="true">${t("thread.inbox.policy.loading")}</p></section>`;
  return html`<section class="thread-inbox" aria-label=${t("thread.inbox.title")}>
    <h4>${t("thread.inbox.write")}</h4>
    <${Help} topic="inbox.section" />
    <p class="muted">${policyOn && !unavailable ? t("thread.inbox.next_boundary") : reason}</p>
    <label for="thread-inbox-kind">${t("thread.inbox.action")}</label>
    <select id="thread-inbox-kind" value=${kind} disabled=${busy || !policyOn || unavailable}
      ...${helpFor("inbox.kind")} onChange=${changeKind}>
      ${MESSAGE_KINDS.map((value) => html`<option value=${value}>${t(KIND_LABELS[value])}</option>`)}
    </select>
    <${Help} topic="inbox.kind" compact=${true} />
    <label for="thread-inbox-text">${t("thread.inbox.message")}</label>
    <textarea id="thread-inbox-text" rows="3" value=${body} disabled=${busy || !policyOn || unavailable}
      ...${helpFor("inbox.text")} onInput=${changeBody}></textarea>
    <${Help} topic="inbox.text" compact=${true} />
    ${notice && html`<p role="status">${notice}</p>`}
    ${!confirming ? html`<button type="button" class="btn" disabled=${Boolean(problem) || busy}
      ...${helpFor("inbox.send")} onClick=${start}>${t("thread.inbox.send")}</button>`
      : html`<div class="thread-inbox-confirm">
        <p>${t("thread.inbox.confirm.question", { action: t(KIND_LABELS[kind]) })}</p>
        <button type="button" class="btn danger" disabled=${busy} ...${helpFor("inbox.confirm")}
          onClick=${send}>${t("thread.inbox.confirm.send")}</button>
        <button type="button" class="btn secondary" disabled=${busy} ...${helpFor("inbox.back")}
          onClick=${() => setConfirming(false)}>${t("thread.inbox.back")}</button>
      </div>`}
    <${Help} topic="inbox.send" compact=${true} />
    <${Help} topic="inbox.confirm" compact=${true} />
    <${Help} topic="inbox.back" compact=${true} />
  </section>`;
}

// ACP-T04: the live conversation with Codex ACP (#new/acp/<id>). It reads the events from the first one, follows the stream from
// the last cursor it holds and, when the stream ends, reads the page again (that is where `active` comes from). A live session
// lives in the memory of the app; a conversation that is not live is read from its saved file, read only (ACP-T05). A 404, or an
// `error` block of the stream, ends the conversation here (deleted, expired, or never saved) and nothing is retried.
const ACP_RECONNECT_MS = 1000;

export function AcpThread({ token, threadId }) {
  const [thread, setThread] = useState(createAcpThread);
  const [error, setError] = useState(null);
  const [busy, setBusy] = useState(false);
  const [text, setText] = useState("");
  const [epoch, setEpoch] = useState(0);   // another turn started: follow again
  const [bundle, setBundle] = useState("");   // the package of a saved conversation, for the link that starts another one
  const [confirming, setConfirming] = useState(false);   // «Borrar esta conversación» asks once before it deletes
  const [deleted, setDeleted] = useState(false);
  const current = useRef(createAcpThread());
  const endRef = useRef(null);
  const commit = (next) => { current.current = next; setThread(next); };

  async function readPage() {
    try {
      commit(mergeAcpPage(current.current, await apiGet(acpPath(threadId, "?after=" + current.current.cursor), token)));
      if (current.current.reload) return readPage();   // a live conversation turned out to be a saved file: read it from its first event
      return true;
    } catch (failure) {
      if (isAcpLost(failure)) commit({ ...current.current, lost: true, active: false, loaded: true });
      else setError(failure);
      return false;
    }
  }

  useEffect(() => {   // a new conversation starts from nothing, before it reads
    current.current = createAcpThread(); setThread(current.current); setError(null); setText("");
    setBundle(""); setConfirming(false); setDeleted(false);
  }, [threadId]);
  useEffect(() => {   // a saved conversation names its package only in the list; read it once
    if (!thread.saved) return undefined;
    let active = true;
    apiGet(ACP_LIST_PATH, token).then((answer) => { if (active) setBundle(acpBundleOf(answer, threadId)); }, () => {});
    return () => { active = false; };
  }, [thread.saved, threadId, token]);
  useEffect(() => {
    let stopped = false;
    let controller = null;
    async function follow() {
      setError(null);
      if (!(await readPage())) return;
      while (!stopped && !current.current.lost && current.current.active) {
        controller = new AbortController();
        try {
          const response = await fetch(acpStreamPath(threadId, current.current.cursor), {
            headers: { ...authHeaders(token), Accept: "text/event-stream", "Last-Event-ID": String(current.current.cursor) },
            cache: "no-store", signal: controller.signal });
          if (response.status === 404) { commit({ ...current.current, lost: true, active: false }); return; }
          if (!response.ok || !response.body) throw new Error("HTTP " + response.status);
          const reader = response.body.getReader();
          const decoder = new TextDecoder();
          let buffer = "";
          for (;;) {
            const { done, value } = await reader.read();
            if (done) break;
            buffer += decoder.decode(value, { stream: true }).replace(/\r\n/g, "\n");
            let end;
            while ((end = buffer.indexOf("\n\n")) >= 0) {
              const block = parseBlock(buffer.slice(0, end));
              buffer = buffer.slice(end + 2);
              if (block) commit(applyAcpStreamBlock(current.current, block));
            }
            if (current.current.lost) return;
          }
        } catch (_) {
          if (stopped) return;
        }
        if (stopped || current.current.lost) return;
        if (!(await readPage())) return;
        if (current.current.active) await new Promise((resolve) => setTimeout(resolve, ACP_RECONNECT_MS));
      }
    }
    follow();
    return () => { stopped = true; if (controller) controller.abort(); };
  }, [threadId, token, epoch]);

  useEffect(() => { if (endRef.current && thread.active) endRef.current.scrollIntoView({ block: "end" }); }, [thread.events.length]);

  async function act(path, body, then) {
    if (busy) return;
    setBusy(true); setError(null);
    try {
      await apiPost(acpPath(threadId, path), token, body);
      then();
      setEpoch((value) => value + 1);
    } catch (failure) {
      if (isAcpLost(failure)) commit({ ...current.current, lost: true, active: false });
      else setError(failure);
    } finally { setBusy(false); }
  }
  const send = () => { if (text.trim()) act("/prompts", { text: text.trim() }, () => setText("")); };
  const cancel = () => act("/cancel", {}, () => {});
  async function remove() {   // only the person presses it: it deletes the file, and closes the session when it is live
    if (busy) return;
    setBusy(true); setError(null);
    try {
      await apiPost(acpDeletePath(threadId), token, {});
      setDeleted(true); setConfirming(false);
      commit({ ...createAcpThread(), loaded: true, lost: true });   // nothing is left to follow
    } catch (failure) {
      if (isAcpLost(failure)) commit({ ...current.current, lost: true, active: false });
      else setError(failure);
      setConfirming(false);
    } finally { setBusy(false); }
  }

  const state = acpComposerState(thread);
  const groups = groupAcpEvents(thread.events);
  if (deleted) {   // the conversation is gone: nothing is left to read, follow or send
    return html`<section class="ns thread-view acp-thread" aria-label=${t("thread.acp.title")}>
      <div class="thread-head"><h3>${t("thread.acp.title")}</h3></div>
      <p class="message ok" role="status">${t("thread.acp.deleted")}</p>
      <p><a class="acp-consent-link" href="#apps">${t("thread.acp.consent_link")}</a></p>
    </section>`;
  }
  return html`<section class="ns thread-view acp-thread" aria-label=${t("thread.acp.title")}>
    <div class="thread-head"><h3>${t("thread.acp.title")}</h3><span class="muted">${threadId}</span>
      <button type="button" class="btn secondary" disabled=${busy} ...${helpFor("thread.refresh")}
        onClick=${() => setEpoch((value) => value + 1)}>${t("thread.refresh")}</button><${Help} topic="thread.refresh" compact=${true} />
      <${Shortcuts} mode=${currentMode()} sessionId="" project=${null} scope="acp" /></div>
    <${Help} topic="acp_thread.section" />
    ${state === "lost" && html`<p class="message error" role="alert">${t("thread.acp.lost")}</p>`}
    ${state === "saved" && html`<div class="acp-saved">
      <p class="message acp-saved-note" role="status">${t("thread.acp.saved")}</p>
      ${thread.redacted && html`<p class="message acp-saved-note" role="status">${t("thread.acp.saved.redacted")}</p>`}
      ${thread.incomplete && html`<p class="message acp-saved-note" role="status">${t("thread.acp.saved.incomplete")}</p>`}
      <p><a class="acp-consent-link" href=${bundle ? acpStartHref(bundle) : "#apps"}>${bundle ? t("thread.acp.saved.new") : t("thread.acp.consent_link")}</a></p>
    </div>`}
    ${acpHistoryWarningKey(thread) && state !== "lost" && html`<p class="message acp-history-note" role="status">${t(acpHistoryWarningKey(thread))}</p>`}
    ${error && html`<p class="message error" role="alert">${t("thread.refresh.error", { message: errorText(error) })}</p>`}
    ${acpNeedsConsentNotice(thread) && state !== "lost" && html`<p class="message acp-consent-note" role="status">${t("thread.acp.consent_missing")}
      <a class="acp-consent-link" href="#apps">${t("thread.acp.consent_link")}</a></p>`}
    ${state === "loading" && !error && html`<p class="muted" aria-busy="true">${t("thread.loading")}</p>`}
    <ol class="thread-list" role="log" aria-label=${t("thread.acp.messages.aria")}>
      ${groups.map((item) => html`<${AcpCard} key=${item.key} item=${item} />`)}
    </ol>
    <div ref=${endRef} class="thread-end" aria-hidden="true"></div>
    ${state === "active" && html`<div class="acp-controls">
      <p class="muted" role="status" aria-live="polite">${t("thread.acp.running")}</p>
      <button type="button" class="btn secondary" disabled=${busy} ...${helpFor("acp_thread.cancel")}
        onClick=${cancel}>${t("thread.acp.cancel")}</button><${Help} topic="acp_thread.cancel" compact=${true} />
    </div>`}
    ${state === "idle" && html`<div class="acp-controls">
      <label for="acp-thread-text">${t("thread.acp.message")}</label>
      <textarea id="acp-thread-text" rows="3" value=${text} disabled=${busy} ...${helpFor("acp_thread.text")}
        onInput=${(event) => setText(event.target.value)}></textarea>
      <${Help} topic="acp_thread.text" compact=${true} />
      <button type="button" class="btn" disabled=${busy || !text.trim()} ...${helpFor("acp_thread.send")}
        onClick=${send}>${t("thread.acp.send")}</button><${Help} topic="acp_thread.send" compact=${true} />
    </div>`}
    ${(state === "saved" || state === "active" || state === "idle") && html`<div class="acp-controls acp-delete">
      ${!confirming && html`<button type="button" class="btn secondary" disabled=${busy} ...${helpFor("acp_thread.delete")}
        onClick=${() => setConfirming(true)}>${t("thread.acp.delete")}</button><${Help} topic="acp_thread.delete" compact=${true} />`}
      ${confirming && html`<div class="acp-confirm" role="group" aria-label=${t("thread.acp.delete")}>
        <p class="message error" role="alert">${t("thread.acp.delete.confirm")}</p>
        <button type="button" class="btn" disabled=${busy} data-help-exempt="confirm" onClick=${remove}>${busy ? t("thread.acp.deleting") : t("thread.acp.delete.yes")}</button>
        <button type="button" class="btn secondary" disabled=${busy} data-help-exempt="confirm" onClick=${() => setConfirming(false)}>${t("thread.acp.delete.keep")}</button>
      </div>`}
    </div>`}
  </section>`;
}

// ACP-T04: the first message of a Codex ACP conversation. The route carries the package; Apps only navigated here. Sending opens the
// live thread in place of this entry, so Back does not return to an empty form.
export function AcpStart({ token, bundle }) {
  const [text, setText] = useState("");
  const [busy, setBusy] = useState(false);
  const [failure, setFailure] = useState(null);
  const [consent, setConsent] = useState(null);   // null while it loads, else the answer of the server
  const body = acpStartBody(bundle, text);

  useEffect(() => {
    let active = true;
    setConsent(null);
    apiGet(acpConsentReadPath(bundle), token).then((payload) => { if (active) setConsent(payload); }, () => { if (active) setConsent({}); });
    return () => { active = false; };
  }, [bundle, token]);

  async function start() {
    if (busy || !body) return;
    setBusy(true); setFailure(null);
    try {
      const id = acpStartedId(await apiPost(ACP_START_PATH, token, body));
      if (id) { location.replace(acpThreadHref(id)); return; }
      setFailure(acpStartFailure(null));
    } catch (error) {
      setFailure(acpStartFailure(error instanceof ApiError ? error : new ApiError({ message: String(error) })));
    }
    setBusy(false);
  }

  return html`<section class="ns thread-view acp-thread acp-start" aria-label=${t("thread.acp.start.title")}>
    <div class="thread-head"><h3>${t("thread.acp.start.title")}</h3></div>
    <p class="acp-start-area">${t("thread.acp.start.area")} <code>${bundle}</code></p>
    ${consent && consent.configured !== true && html`<p class="message acp-consent-note" role="status">${t("thread.acp.consent_missing")}
      <a class="acp-consent-link" href="#apps">${t("thread.acp.consent_link")}</a></p>`}
    <div class="acp-controls">
      <label for="acp-start-text">${t("thread.acp.start.label")}</label>
      <textarea id="acp-start-text" rows="4" value=${text} disabled=${busy} ...${helpFor("acp_thread.start_text")}
        onInput=${(event) => setText(event.target.value)}></textarea>
      <${Help} topic="acp_thread.start_text" compact=${true} />
      <button type="button" class="btn" disabled=${busy || !body} ...${helpFor("acp_thread.start")}
        onClick=${start}>${busy ? t("thread.acp.start.starting") : t("thread.acp.start.button")}</button><${Help} topic="acp_thread.start" compact=${true} />
    </div>
    ${failure && html`<div class="acp-unavailable" role="alert">
      ${failure.titleKey && html`<p class="acp-unavailable-title">${t(failure.titleKey)}</p>`}
      <p class="message error">${failure.message || t(failure.textKey)}</p>
      ${failure.unavailable && html`<div class="acp-controls">
        <a class="btn secondary" href="#new" ...${helpFor("acp_thread.cli")}>${t("thread.acp.choose_cli")}</a><${Help} topic="acp_thread.cli" compact=${true} />
      </div>`}
    </div>`}
  </section>`;
}
