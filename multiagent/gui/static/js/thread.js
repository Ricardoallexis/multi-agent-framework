// Copyright 2026 Ricardoallexis and contributors
// SPDX-License-Identifier: Apache-2.0
// The run conversation in Nueva sesión. Reads only while this view is mounted and visible.
import { html, useState, useEffect, useRef } from "../vendor/htm-preact-standalone.module.js";
import { apiGet, apiPost, runPath } from "./api.js";
import { HumanActions } from "./human-actions.js";
import { Help, helpFor } from "./help.js";
import { errorText } from "./text.js";
import { ThreadCard } from "./thread-cards.js";
import { DocumentCard } from "./document-card.js";
import { currentMode } from "./gui-mode.js";   // SIM-T03
import { POLL_MS, composerState, createThread, entryKey, groupThread, isNearEnd, isTerminal, mergeThread, pollAfter,
  nextFollowing, shouldFollow, SMOOTH_MS, tailKey } from "./thread-model.js";
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
      setError(reviewFailure);
    } catch (failure) {
      if (request === ticket.current) setError(failure);
    } finally {
      if (request === ticket.current) inFlight.current = false;
    }
  }

  useEffect(() => {
    current.current = createThread(); setThread(current.current); setReviewView(null); setRunLimit(null); setError(null);
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

  async function act(action, body) {
    if (busy) return;
    setBusy(true); setError(null);
    try {
      await apiPost(runPath(runId, action), token, body);
      await load(true);
    } catch (failure) {
      setError(failure);
      if (failure.status === 409 || failure.status === 404) await load(true);
    } finally { setBusy(false); }
  }

  return html`<section class="ns thread-view" aria-label=${t("thread.title")}>
    <div class="thread-head"><h3>${t("thread.title")}</h3><span class="muted">${runId}</span>
      <button type="button" class="btn secondary" disabled=${busy} ...${helpFor("thread.refresh")}
        onClick=${() => load(true)}>${t("thread.refresh")}</button><${Help} topic="thread.refresh" compact=${true} /></div>
    <${Help} topic="thread.section" />
    ${error && html`<p class="message error" role="alert">${t("thread.refresh.error", { message: errorText(error) })}</p>`}
    ${!thread.loaded && !error && html`<p class="muted" aria-busy="true">${t("thread.loading")}</p>`}
    <ol class="thread-list" role="log" aria-label=${t("thread.messages.aria")}>
      ${groupThread(thread.entries, thread.status).map((item) => item.type === "entry"
        ? html`<${ThreadCard} key=${entryKey(item.entry)} entry=${item.entry} status=${thread.status}
          runId=${runId} token=${token} reviewView=${reviewView} busy=${busy} onAction=${act} limit=${runLimit} mode=${currentMode()} />`
        : html`<li key=${item.key} class="thread-fold"><details class="thread-earlier-reviews">
          <summary>${t("thread.earlier_reviews")} (${item.entries.length})</summary>
          <ol class="thread-list">${item.entries.map((entry) => html`<${ThreadCard} key=${entryKey(entry)} entry=${entry}
            status=${thread.status} runId=${runId} token=${token} reviewView=${null} busy=${busy} onAction=${act}
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
