// Copyright 2026 Ricardoallexis and contributors
// SPDX-License-Identifier: Apache-2.0
// Entry module of the extension «continuity» (CNT-T06), loaded by the page like every folder of static/extensions/ (js/extensions.js).
// It adds three things and edits no shared file of the shell:
//   the screen «Decisiones»        #continuity            the decision card of every task that waits for a usage limit
//   the right-panel section        «Continuidad»          the card of the task held on the session that is open
//   the section «Continuidad» of Uso                      the mode, threshold, candidates and hand-off limit (read only)
// The data comes from multiagent/gui/continuity_api.py; the node «En espera · hasta HH:MM» of the agent tree comes from the
// children of the session (the tree already draws that state, js/agent-tree-model.js).
import "./i18n.js";
import { registerHelp, registerUsageSection, registerView } from "../../js/extensions.js";
import { registerSlot } from "../../js/panel-slots.js";
import { HELP } from "./help.js";
import { ContinuityPolicy } from "./policy.js";
import { ContinuitySlot } from "./slot.js";
import { ContinuityView } from "./view.js";

registerHelp(HELP, "continuity");
registerView({ id: "continuity", owner: "continuity", group: "pending", pendingType: "continuity", icon: "shield", label: "Decisiones", topic: "continuity.view", component: ContinuityView, order: 50, mode: "simple" });
registerSlot("continuity", ContinuitySlot, { title: "Continuidad", topic: "continuity.slot", owner: "continuity", order: 50 });
registerUsageSection({ id: "continuity", owner: "continuity", label: "Continuidad", component: ContinuityPolicy, order: 50 });
