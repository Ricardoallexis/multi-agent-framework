// Copyright 2026 Ricardoallexis and contributors
// SPDX-License-Identifier: Apache-2.0
// One item of a session, by kind. Everything a session holds is untrusted: it is rendered only as text
// nodes (never as HTML), the model's reasoning is never shown, and long results start collapsed.
import { html } from "../vendor/htm-preact-standalone.module.js";
import { t, currentLanguage } from "./i18n.js";
import { routeHref } from "./route.js";
import { kindLabel, tokensText } from "./session-text.js";
import { fmtDate } from "./text.js";
import { foldWork, workLabel } from "./work-fold.js";

const SHORT_NOTE = 240;
const CHILD_ID = /^(?:claude|codex|copilot|framework):[A-Za-z0-9:_-]{1,190}$/;
const MAX_FILES = 5;

function When({ item }) {
  if (!item.timestamp) return null;
  return html`<time class="when muted small" datetime=${item.timestamp}>${fmtDate(item.timestamp)}</time>`;
}

function Truncated({ item }) {
  return item.truncated ? html`<p class="note small">${t("sessions.truncated")}</p>` : null;
}

function Message({ item, who, mode = "advanced" }) {
  const usage = item.usage && mode !== "simple" ? tokensText(item.usage, { brief: true }) : "";   // SIM-T03: no tokens in simple mode
  return html`<li class=${"item msg " + item.kind}>
    <div class="item-head"><strong>${who}</strong>${item.model && html`<span class="muted small"> · ${item.model}</span>`}
      <${When} item=${item} /></div>
    <div class="message-text">${item.text || html`<span class="muted">${t("sessions.no_text")}</span>`}</div>
    <${Truncated} item=${item} />
    ${usage && usage !== t("sessions.unknown") && html`<p class="muted small">${t("sessions.message_tokens")}${usage}</p>`}
  </li>`;
}

function ToolCall({ item }) {
  const tool = item.tool || {};
  const files = item.files || [];
  return html`<li class="item tool-call">
    <div class="item-head"><strong>${t("sessions.tool")}</strong> <code>${tool.name || t("sessions.no_name")}</code><${When} item=${item} /></div>
    ${tool.summary && html`<div class="tool-summary">${tool.summary}</div>`}
    ${files.length > 0 && html`<p class="muted small">${t("sessions.files")}${files.slice(0, MAX_FILES).join(", ")}${files.length > MAX_FILES ? t("sessions.more_files") + (files.length - MAX_FILES) + t("sessions.more") : ""}</p>`}
  </li>`;
}

function ToolResult({ item }) {
  const text = item.text || "";
  const size = text ? t("sessions.character_count", { count: text.length.toLocaleString(currentLanguage()) }) : "";
  return html`<li class="item tool-result">
    <details>
      <summary>${t("sessions.tool_result")}${size}<${When} item=${item} /></summary>
      ${text ? html`<pre class="data" tabindex="0">${text}</pre>` : html`<p class="muted">${t("sessions.no_content")}</p>`}
      <${Truncated} item=${item} />
    </details>
  </li>`;
}

// Small notes (reasoning marker, compaction, system, unknown): short text inline, long text collapsed.
function Note({ item, label, extra }) {
  const text = item.text || "";
  const long = text.length > SHORT_NOTE;
  return html`<li class=${"item note-item " + item.kind}>
    ${long
      ? html`<details><summary><span class="note-label">${label}</span> · ${text.slice(0, SHORT_NOTE)}…<${When} item=${item} /></summary>
          <pre class="data" tabindex="0">${text}</pre></details>`
      : html`<p class="note small"><span class="note-label">${label}</span>${text && html`<span> · ${text}</span>`}${extra}
          <${When} item=${item} /></p>`}
    <${Truncated} item=${item} />
  </li>`;
}

function Subagent({ item }) {
  const linked = typeof item.child_id === "string" && CHILD_ID.test(item.child_id);
  return html`<li class="item note-item subagent">
    <p class="note small"><span class="note-label">${t("sessions.subagent")}</span>${item.text && html`<span> · ${item.text}</span>`}
      ${linked && html` · <a href=${routeHref("sessions", item.child_id)}>${t("sessions.open_subagent")}</a>`}
      <${When} item=${item} /></p>
  </li>`;
}

export function Item({ item, mode = "advanced" }) {
  switch (item.kind) {
    case "user_message": return html`<${Message} item=${item} who=${t("sessions.user")} mode=${mode} />`;
    case "assistant_message": return html`<${Message} item=${item} who=${t("sessions.assistant")} mode=${mode} />`;
    case "tool_call": return html`<${ToolCall} item=${item} />`;
    case "tool_result": return html`<${ToolResult} item=${item} />`;
    case "reasoning_omitted":
      return html`<${Note} item=${item} label=${t("sessions.reasoning_omitted")} extra=${html` <span class="muted">${t("sessions.viewer_omits_reasoning")}</span>`} />`;
    case "compaction": return html`<${Note} item=${item} label=${t("sessions.context_compacted")} />`;
    case "system": return html`<${Note} item=${item} label=${t("sessions.system")} />`;
    case "subagent": return html`<${Subagent} item=${item} />`;
    case "usage":
      return html`<${Note} item=${{ ...item, text: t("sessions.tokens") + tokensText(item.usage) }} label=${kindLabel("usage")} />`;
    default:
      return html`<${Note} item=${{ ...item, text: "" }} label=${t("sessions.unknown_item")} />`;
  }
}

// A folded run of work (GUI2-T02): closed by default, it opens to show every tool call and result inside.
function WorkBlock({ block, mode }) {
  return html`<li class="item work-fold">
    <details>
      <summary>${workLabel(block)}</summary>
      <ol class="items work-items" aria-label=${t("sessions.work_done")}>
        ${block.entries.map((entry) => html`<${Item} key=${entry.index} item=${entry.item} mode=${mode} />`)}
      </ol>
    </details>
  </li>`;
}

export function Items({ items, mode = "advanced" }) {
  return html`<ol class="items" aria-label=${t("sessions.content")}>
    ${foldWork(items).map((block) => (block.fold
      ? html`<${WorkBlock} key=${"w" + block.start} block=${block} mode=${mode} />`
      : html`<${Item} key=${block.index} item=${block.item} mode=${mode} />`))}
  </ol>`;
}
