// Copyright 2026 Ricardoallexis and contributors
// SPDX-License-Identifier: Apache-2.0
import { mount as mountCreateRun } from "/ui/static/js/create-run.js";

const context = {};
mountCreateRun(document.querySelector("#catalog"), context);

for (const [id, path, label] of [
  ["run-view", "/ui/static/js/run-view.js", "Run view"],
  ["human-actions", "/ui/static/js/human-actions.js", "Human actions"],
]) {
  try {
    const module = await import(path);
    module.mount(document.querySelector(`#${id}`), context);
  } catch {
    const status = document.createElement("p");
    status.className = "muted";
    status.setAttribute("role", "status");
    status.textContent = `${label} is unavailable. Reload when its UI module has been delivered.`;
    document.querySelector(`#${id}`).append(status);
  }
}
