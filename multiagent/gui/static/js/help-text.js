// Copyright 2026 Ricardoallexis and contributors
// SPDX-License-Identifier: Apache-2.0
// Contextual help (GUI-T12): every control and section of the screen has a short hint (one line, always visible)
// and a longer explanation (2 to 4 sentences, behind the "?" button). Plain text, no jargon.
// This legacy Spanish catalog is a fallback while entries move to per-view language dictionaries.
// The entries live in one part per view (help-text-<part>.js), each a block of strict JSON so static tests can read
// it without running the browser: double quotes, no trailing commas, no comments, one sentence per line.
// Each entry is { title, hint, more }: title names the control for screen readers, hint is the visible line and
// more is the list of sentences that opens with the "?" button. The key is what the view passes to <Help topic=...>.
// tests/test_gui_help.py checks that every control of the views has an entry and that the texts stay short.
import { t } from "./i18n.js";
import { HELP as SHELL_HELP } from "./help-text-shell.js";
import { HELP as RUNS_HELP } from "./help-text-runs.js";
import { HELP as TEAMS_HELP } from "./help-text-teams.js";
import { HELP as LISTS_HELP } from "./help-text-lists.js";
import { HELP as APPS_HELP } from "./help-text-apps.js";

// The entries built from data for the run form live with its texts.
export { MODE_HINTS, fieldHelpEntry, modeHelpEntry } from "./help-text-shell.js";

// The order of the topics before the catalog was split, which is the order Ctrl+K lists help results in
// (command-palette-model.js). A topic missing here comes after these, in the order of its part. Strict JSON too.
const ORDER = [
  "work_map.refresh", "work_map.search", "work_map.active", "work_map.fold_finished", "work_map.tree", "work_map.list",
  "work_map.fold", "work_map.open_session", "work_map.assignment_edge", "work_map.notice_edge", "runs.section",
  "runs.new", "runs.list", "runs.back", "run.open_chat", "human.bar_focus", "create.back", "create.bundle",
  "create.workflow", "create.steps", "create.project", "create.inputs.required", "create.inputs.optional",
  "create.inputs.context", "create.mode", "create.operating_mode", "create.sensitive", "create.confirm_real",
  "create.submit", "create.cancel", "preview.panel", "preview.copy", "run.refresh", "run.live", "run.steps",
  "run.skipped", "run.role", "run.operating_mode", "run.counters", "counter.model_calls", "counter.human_steps",
  "counter.steps", "counter.time", "run.history", "run.results", "run.compare_select", "run.compare", "run.inputs",
  "run.events", "human.section", "human.approve", "human.tool_approve", "human.regenerate", "human.changes",
  "human.comment_add", "human.comment_target", "human.comment_kind", "human.comment_text", "human.comment_quote",
  "human.comment_remove", "human.reject", "human.reject_reason", "human.cancel", "human.prompt", "human.copy_prompt",
  "human.response", "human.provider", "human.model", "human.prompt_used", "human.notes", "external.section",
  "external.output", "external.evidence", "external.preview", "external.confirm", "human.check", "human.submit",
  "sessions.list", "sessions.refresh", "sessions.source", "sessions.surface", "sessions.text", "sessions.cwd",
  "sessions.since", "sessions.until", "sessions.subagents", "sessions.search", "sessions.clear", "sessions.warnings",
  "sessions.more", "sessions.retry", "sessions.empty", "sessions.roots", "session.back", "session.run_link",
  "session.refresh", "session.facts", "session.live", "session.pause", "session.retry", "session.warnings",
  "session.items", "session.reasoning", "session.more", "session.items_retry", "open.claude", "open.codex",
  "usage.section", "usage.plans", "usage.global", "usage.period", "usage.costs", "usage.refresh", "usage.view",
  "usage.plan_notes", "usage.cost_reasons", "usage.cost_evidence", "evaluation.section", "evaluation.refresh",
  "evaluation.sets", "evaluation.runs", "evaluation.compare", "evaluation.cases", "evaluation.fingerprint",
  "teams.section", "teams.refresh", "teams.list", "teams.decision", "teams.approve", "teams.reject", "teams.note",
  "teams.chart", "teams.links", "teams.mermaid", "teams.copy", "teams.versions", "teams.diff", "plans.section",
  "plans.refresh", "plans.filter", "plans.list", "plans.detail", "plans.history", "plans.edit", "plans.reassign",
  "plans.approve", "plans.reject", "plans.advance", "plans.note", "open.copilot", "open.copy", "subagents.panel",
  "subagents.refresh", "subagents.retry", "subagents.back", "subagents.tabs", "subagents.item", "subagents.more",
  "agent_tree.open", "agent_tree.view", "agent_tree.back", "agent_tree.filter", "agent_tree.node", "agent_tree.log",
  "agent_tree.open_child", "shell.menu", "shell.help_mode", "shell.panel", "shell.new_session", "chat.welcome",
  "chat.office", "chat.starters", "chat.composer", "chat.send", "chat.cloud_confirm", "chat.skip", "chat.back",
  "chat.restart", "chat.form", "chat.suggestion", "chat.all", "chat.choice", "chat.runs", "chat.question",
  "chat.confirm", "chat.routing", "chat.session_mode", "chat.connect_other", "chat.single_model", "chat.single_task",
  "chat.model_picker", "chat.model_choice", "chat.model_disconnected", "chat.model_retry", "chat.launch", "chat.edit",
  "chat.retry", "thread.section", "thread.refresh", "thread.detail", "thread.related", "thread.approve",
  "thread.changes", "thread.reject", "thread.feedback", "thread.comment", "thread.confirm", "thread.output",
  "thread.json", "thread.document", "thread.document_save", "thread.previous", "thread.calls", "team_card.mode",
  "team_card.section", "team_card.diff", "team_card.approve", "team_card.reject", "team_card.note",
  "team_card.confirm", "team_card.open", "team_card.refresh", "shell.search", "shell.activities", "panel.changes",
  "panel.tokens", "panel.sources", "panel.retry", "panel.more", "panel.by_model", "panel.cost_evidence",
  "pending.toggle", "pending.open", "pending.refresh", "pending.notifications", "pending.tabs", "inbox.section",
  "inbox.kind", "inbox.text", "inbox.send", "inbox.confirm", "inbox.back", "pending.background", "pending.cancel",
  "pending.cancel_confirm", "pending.cancel_back", "human.tool_preview", "chat.executor_launch", "plan_card.inputs",
  "plan_card.approve", "plan_card.reject", "plan_card.note", "plan_card.confirm", "plan_card.team", "plan_card.run",
  "plan_card.open", "plan_card.refresh", "chat.limit_notice", "chat.limit_suggestion", "chat.connection_checks",
  "shell.more", "shell.more_notice", "run.limit_retry", "run.tabs", "chat.box_menu", "chat.project_select",
  "palette.search", "palette.close", "customize.pin", "customize.card", "customize.help", "customize.reset",
  "shell.customize", "customize.accent", "customize.language", "plan.auto_advance", "customize.advanced_mode",
  "shell.advanced_only", "packages.install_zip", "packages.example", "packages.trust_functions", "packages.remove",
  "packages.degraded", "chat.plan_draft", "chat.office_plan", "chat.plan_save", "start.steps", "start.times",
  "start.real_models", "start.office_example", "start.office_import", "shell.instance_label"
];

const PARTS = { ...SHELL_HELP, ...RUNS_HELP, ...TEAMS_HELP, ...LISTS_HELP, ...APPS_HELP };
const LISTED = new Set(ORDER);
const KEYS = [...ORDER.filter((key) => Object.hasOwn(PARTS, key)), ...Object.keys(PARTS).filter((key) => !LISTED.has(key))];
export const HELP = Object.fromEntries(KEYS.map((key) => [key, PARTS[key]]));

// Prefer a view's language dictionary. The legacy catalog remains as a fallback
// until every view has been moved to its own dictionary.
export function localizedHelp(topic) {
  const key = "help." + topic;
  const translated = t(key);
  return typeof translated === "object" ? translated : HELP[topic];
}

/* GUI3-T08 */
