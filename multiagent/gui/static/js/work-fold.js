// Copyright 2026 Ricardoallexis and contributors
// SPDX-License-Identifier: Apache-2.0
// Folds the work of a session (GUI2-T02): a run of consecutive tool calls, tool results, omitted-reasoning markers and
// usage notes becomes one block "Trabajó N min · K herramientas" that opens to show the items. Pure functions (no DOM,
// no Preact) so they can be checked on their own; session-items.js draws the blocks.

// Kinds that belong to the work of the model. Messages, system notes, compactions, subagent notices and unknown kinds
// are never folded: they are the conversation itself.
const WORK_KINDS = new Set(["tool_call", "tool_result", "reasoning_omitted", "usage"]);

function toMs(value) {
  if (!value) return null;
  const ms = new Date(value).getTime();
  return Number.isNaN(ms) ? null : ms;
}

// Seconds between the first and the last timestamp of the entries, or null when fewer than two are readable.
function spanSeconds(entries) {
  const stamps = entries.map((entry) => toMs(entry.item.timestamp)).filter((ms) => ms !== null);
  if (stamps.length < 2) return null;
  return Math.max(0, (Math.max(...stamps) - Math.min(...stamps)) / 1000);
}

// items -> blocks in order. A block is { fold: false, index, item } for one item that stays as it is, or
// { fold: true, start, entries: [{ index, item }], tools, seconds } for a folded run. A run with no tool call in it
// (only results, markers or usage) stays unfolded: "0 herramientas" would say nothing.
export function foldWork(items) {
  const blocks = [];
  let run = [];
  const flush = () => {
    if (!run.length) return;
    const tools = run.filter((entry) => entry.item.kind === "tool_call").length;
    if (tools > 0) {
      blocks.push({ fold: true, start: run[0].index, entries: run, tools, seconds: spanSeconds(run) });
    } else {
      for (const entry of run) blocks.push({ fold: false, index: entry.index, item: entry.item });
    }
    run = [];
  };
  items.forEach((item, index) => {
    if (WORK_KINDS.has(item.kind)) {
      run.push({ index, item });
    } else {
      flush();
      blocks.push({ fold: false, index, item });
    }
  });
  flush();
  return blocks;
}

// "Trabajó 3 min · 5 herramientas". Under half a minute it says "menos de 1 min"; without timestamps, no duration.
export function workLabel(block) {
  const tools = block.tools === 1 ? "1 herramienta" : block.tools + " herramientas";
  if (block.seconds === null) return "Trabajó · " + tools;
  const time = block.seconds < 30 ? "menos de 1 min" : Math.max(1, Math.round(block.seconds / 60)) + " min";
  return "Trabajó " + time + " · " + tools;
}
