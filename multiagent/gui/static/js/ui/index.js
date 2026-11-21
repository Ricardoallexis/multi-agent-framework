// Copyright 2026 Ricardoallexis and contributors
// SPDX-License-Identifier: Apache-2.0
// Shared Preact components of the GUI (GUI2-T11). Import them from here or from their own file; every file starts with
// a comment block that documents its props. In short:
//
//   StatusChip  status-chip.js  <${StatusChip} status labels="run|step|session|subagent|dot|<map>" variant="chip|inline|dot" detail />
//   Progress    progress.js     <${Progress} label value max valueText tone />            role="progressbar" + aria-value*
//   Tabs        tabs.js         <${Tabs} id label tabs=[{id,label,count}] selected onSelect after panelProps>panel<//>
//   LevelBadge  level-badge.js  <${LevelBadge} level="T1".."T4" showName />
//   labels.js   the label maps (tone vocabulary, default sets, registerLabelSet) that StatusChip reads
//
// Rules for every new view that uses them: no colour-only meaning (a text always says it), no inline style (CSP),
// 44 px hit areas, visible focus (app.css does it for buttons, links, tabs and summaries), plain text only.
export { StatusChip } from "./status-chip.js";
export { Progress, progressInfo } from "./progress.js";
export { Tabs, nextTabIndex, tabDomIds } from "./tabs.js";
export { LevelBadge, LEVELS, levelInfo } from "./level-badge.js";
export { Icon, ICON_NAMES } from "./icon.js";
export {
  DEFAULT_LABELS, TONES, labelEntry, labelSet, normalizeTone, registerLabelSet, resetLabelSets, resolveLabels, statusText,
} from "./labels.js";
