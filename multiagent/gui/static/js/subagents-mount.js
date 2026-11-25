// Copyright 2026 Ricardoallexis and contributors
// SPDX-License-Identifier: Apache-2.0
// Mount of the subagents panel (subagents-panel.js) inside the «Subagentes» section of the right panel of the shell
// (panel-slots.js draws it; there is no column of its own any more). It decides WHICH session's children to list while
// `openId` (the session open in the centre, or null) is shown, and draws the panel for it.
// Open a subagent's own conversation and the panel keeps listing its siblings (those of the main session).
import { html, useState, useEffect } from "../vendor/htm-preact-standalone.module.js";
import { apiGet } from "./api.js";
import { t } from "./i18n.js";
import { SubagentsPanel } from "./subagents-panel.js";

const CLAUDE_SUBAGENT = /^(claude:[0-9a-fA-F-]{36}):agent-[A-Za-z0-9_-]{1,64}$/;
const CODEX_THREAD = /^codex:[0-9a-fA-F-]{36}$/;

// Which session's children the panel lists while `id` is open. A Claude subagent is named after its parent;
// a Codex thread says its parent in its summary (looked up once); anything else lists its own children.
// null: no session is open; undefined: a Codex thread whose parent is still being asked for.
export function useListedSession(token, id) {
  const [parents, setParents] = useState({});
  useEffect(() => {
    if (!id || !CODEX_THREAD.test(id) || id in parents) return undefined;
    let cancelled = false;
    apiGet("/gui/api/sessions/" + encodeURIComponent(id), token)
      .then((summary) => { if (!cancelled) setParents((previous) => ({ ...previous, [id]: summary.parent_id || null })); })
      .catch(() => { if (!cancelled) setParents((previous) => ({ ...previous, [id]: null })); });
    return () => { cancelled = true; };
  }, [id, token]);
  if (!id) return null;
  const claude = CLAUDE_SUBAGENT.exec(id);
  if (claude) return claude[1];
  if (CODEX_THREAD.test(id) && !(id in parents)) return undefined; // still asking: show nothing yet
  return parents[id] || id;
}

export function SubagentsMount({ token, openId = null, onContent }) {
  const listed = useListedSession(token, openId);
  useEffect(() => { if (!listed && typeof onContent === "function") onContent({ has: false }); }, [listed, onContent]);   // GUI3-T04
  if (!listed) {
    return html`<p class="muted small slot-empty">${openId ? t("subagents.finding_main") : t("subagents.open_session")}</p>`;
  }
  return html`<${SubagentsPanel} key=${listed} token=${token} sessionId=${listed} currentId=${openId} onContent=${onContent} />`;
}
