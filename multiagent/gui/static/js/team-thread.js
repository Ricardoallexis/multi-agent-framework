// Copyright 2026 Ricardoallexis and contributors
// SPDX-License-Identifier: Apache-2.0
// CHT-T05: the chat view of a team version ("#new/team/<version id>"): a thread of one stop with the team card and the
// Approve and Reject buttons. It reads once when it opens and again after a decision or when the person asks.
import { html, useEffect, useRef, useState } from "../vendor/htm-preact-standalone.module.js";
import { apiGet } from "./api.js";
import { Help, helpFor } from "./help.js";
import { errorText } from "./text.js";
import { TeamVersionCard } from "./team-card.js";
import { t } from "./i18n.js";

export function TeamThread({ token, versionId }) {
  const [state, setState] = useState({ loaded: false, entries: [], error: null });
  const ticket = useRef(0);
  const load = async () => {
    const mine = ++ticket.current;
    try {
      const page = await apiGet("/gui/api/threads/teams/" + encodeURIComponent(versionId), token);
      if (mine === ticket.current) setState({ loaded: true, entries: page.entries || [], error: null });
    } catch (error) {
      if (mine === ticket.current) setState((previous) => ({ ...previous, loaded: true, error }));
    }
  };
  useEffect(() => {
    setState({ loaded: false, entries: [], error: null });
    load();
    return () => { ticket.current += 1; };
  }, [versionId, token]);
  return html`<section class="ns thread-view" aria-label=${t("teams.thread.aria")}>
    <div class="thread-head"><h3>${t("teams.thread.title")}</h3><span class="muted">${versionId}</span>
      <button type="button" class="btn secondary" ...${helpFor("team_card.refresh")} onClick=${load}>${t("teams.page.refresh")}</button>
      <${Help} topic="team_card.refresh" compact=${true} /></div>
    ${state.error && html`<p class="message error" role="alert">${t("teams.thread.read_error")}: ${errorText(state.error)}</p>`}
    ${!state.loaded && html`<p class="muted" aria-busy="true">${t("teams.loading.version")}</p>`}
    <ol class="thread-list" aria-label=${t("teams.thread.title")}>
      ${state.entries.map((entry) => html`<${TeamVersionCard} key=${entry.seq} entry=${entry} token=${token} onDone=${load} />`)}
    </ol>
  </section>`;
}
