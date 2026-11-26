// Copyright 2026 Ricardoallexis and contributors
// SPDX-License-Identifier: Apache-2.0
// The selector reads only connection facts. Launching belongs to the session flow.
import { html, useEffect, useRef, useState } from "../vendor/htm-preact-standalone.module.js";
import { helpFor } from "./help.js";
import { AUTO_EFFORTS, buildModelPicker, pickerGroups } from "./model-picker-model.js";
import { errorText, fmtDate } from "./text.js";
import { t } from "./i18n.js";

export function ModelPicker({ connections, selectedId, onSelect, loading, error, onRetry, onConnect, help = {},
  composerOptions = null, onAutoChange = null, onEffortChange = null, onSelectExecutor = null }) {
  const hasAutoCallbacks = typeof onAutoChange === "function" || typeof onEffortChange === "function";
  const hasNewPicker = hasAutoCallbacks || typeof onSelectExecutor === "function";
  const [open, setOpen] = useState(false);
  const [auto, setAuto] = useState(hasAutoCallbacks);
  const [effort, setEffort] = useState("medium");
  const [query, setQuery] = useState("");
  const triggerRef = useRef(null);
  const popoverRef = useRef(null);
  const picker = buildModelPicker(connections, selectedId);
  const selected = picker.models.find((item) => item.id === picker.selectedId);
  const groups = pickerGroups(connections, composerOptions, query);
  useEffect(() => {
    if (!open || !hasNewPicker) return;
    const popover = popoverRef.current;
    popover?.querySelector("input:not([disabled]), button:not([disabled]), select:not([disabled])")?.focus();
    const closeOutside = (event) => {
      if (!popover?.contains(event.target) && !triggerRef.current?.contains(event.target)) setOpen(false);
    };
    document.addEventListener("pointerdown", closeOutside);
    return () => document.removeEventListener("pointerdown", closeOutside);
  }, [open, hasNewPicker]);
  const choose = (item) => {
    if (!item.selectable) return;
    if (item.kind === "executor") {
      onSelectExecutor?.(item.id);
      setOpen(false);
      return;
    }
    onSelect?.(item.id);
    setOpen(false);
  };
  const selectModel = (event) => {
    if (event.target.value === "connect") onConnect();
    else onSelect?.(event.target.value || null);
    setOpen(false);
  };
  return html`<div class="ns-model-select">
    <label id="ns-model-label" for=${hasNewPicker ? undefined : "ns-model-select"}>${t("model_picker.model")}</label>
    ${!hasNewPicker && html`<select id="ns-model-select" value=${picker.selectedId || ""} ...${helpFor("chat.model_picker")}
      onChange=${selectModel}>
      <option value="">${picker.models.length ? t("model_picker.choose_model") : t("model_picker.connect_model")}</option>
      ${picker.models.map((item) => html`<option key=${item.id} value=${item.id}>${item.label}${item.verificationLabel ? " · " + item.verificationLabel : ""}</option>`)}
      ${typeof onConnect === "function" && html`<option value="connect">${t("model_picker.connect_another", { count: picker.disconnected.length })}</option>`}
    </select>`}
    ${hasNewPicker && html`<button type="button" class="ns-auto-trigger" aria-expanded=${open ? "true" : "false"}
      aria-controls="ns-auto-popover" aria-labelledby="ns-model-label" ref=${triggerRef} ...${helpFor("chat.model_picker")}
      onClick=${() => setOpen(!open)}>${auto ? t("model_picker.auto") : selected?.label || t("model_picker.choose_model")}</button>`}
    ${hasNewPicker && open && html`<div id="ns-auto-popover" class="ns-auto-popover popover-surface" role="dialog"
      aria-label=${t("model_picker.model")} ref=${popoverRef}
      onKeyDown=${(event) => {
        if (event.key !== "Escape" || event.defaultPrevented) return;
        event.preventDefault();
        setOpen(false);
        triggerRef.current?.focus();
      }}>
      ${hasAutoCallbacks && html`<div class="ns-auto-switch-row"><div><strong>${t("model_picker.auto")}</strong>
        <small>${t("model_picker.auto_description")}</small></div>
        ${typeof onAutoChange === "function" && html`<button type="button" class=${"switch" + (auto ? " on" : "")} role="switch"
          aria-checked=${auto ? "true" : "false"} aria-label=${t("model_picker.auto")} ...${helpFor("chat.model_picker")}
          onClick=${() => { setAuto(!auto); onAutoChange(!auto); }}></button>`}</div>`}
      ${auto && hasAutoCallbacks ? html`<div class="ns-auto-on">
        ${typeof onEffortChange === "function" && html`<div><p>${t("model_picker.effort")}</p>
        <div class="ns-auto-efforts" role="group" aria-label=${t("model_picker.effort")}>
          ${AUTO_EFFORTS.map((value) => html`<button type="button" key=${value} class=${effort === value ? "active" : ""}
            aria-pressed=${effort === value ? "true" : "false"} onClick=${() => { setEffort(value); onEffortChange(value); }}
            ...${helpFor("chat.model_picker")}>${t("model_picker.effort_" + value)}</button>`)}</div>
        </div>`}
        <p class="ns-auto-note">${t("model_picker.auto_respects")}</p></div>`
      : html`<div class="ns-auto-off"><input type="search" value=${query} onInput=${(event) => setQuery(event.target.value)}
          aria-label=${t("model_picker.search")} placeholder=${t("model_picker.search")} ...${helpFor("chat.model_picker")} />
        ${[["apps", "apps_pc"], ["local", "local_pc"], ["cloud", "cloud_services"]].map(([key, label]) =>
          groups[key].length && html`<section class="ns-auto-group" key=${key}><h3>${t("model_picker." + label)}</h3>
            ${groups[key].map((item) => item.kind === "executor" && (!onSelectExecutor || (item.id === "claude_cli" && !item.selectable))
              ? html`<button type="button" key=${item.id} class="ns-auto-connect" disabled=${typeof onConnect !== "function"}
                  onClick=${onConnect} ...${helpFor("chat.connect_other")}>
                  <span><strong>${item.id === "claude_cli" ? t("model_picker.claude_code_name") : item.label}</strong>
                    <small>${item.reason || (item.selectable ? t("model_picker.ready") : t("model_picker.not_connected"))}</small></span>
                  <span>${item.selectable && item.quota[0] ? item.quota[0] + " · " : ""}${t("model_picker.apps_agents_link")}</span></button>`
              : html`<button type="button" key=${item.id} class="ns-auto-option"
              disabled=${!item.selectable || (item.kind === "executor" ? typeof onSelectExecutor !== "function" : typeof onSelect !== "function")}
              onClick=${() => choose(item)} ...${helpFor("chat.model_picker")}>
              <span><strong>${item.label}</strong><small>${item.selectable
                ? item.modelVariants.length ? item.modelVariants.map((variant) =>
                  variant.base + (variant.efforts.length ? " · " + variant.efforts.map((value) => t("model_picker.effort_" + value)).join(" / ") : "")).join("; ")
                  : item.models.length ? item.models.join(" · ") : item.contextWindow ? t("model_picker.context_window", { count: item.contextWindow }) : t("model_picker.connected")
                : item.reason || t("model_picker.not_connected")}</small></span>
              <span class="ns-auto-quota">${item.selectable ? item.quota[0] || t("model_picker.ready") : ""}</span>
            </button>`)}</section>`)}
        <select id="ns-model-select" value=${picker.selectedId || ""} ...${helpFor("chat.model_picker")}
          aria-label=${t("model_picker.model")} onChange=${selectModel}>
      <option value="">${picker.models.length ? t("model_picker.choose_model") : t("model_picker.connect_model")}</option>
      ${picker.models.map((item) => html`<option key=${item.id} value=${item.id}>${item.label}${item.verificationLabel ? " · " + item.verificationLabel : ""}</option>`)}
      ${typeof onConnect === "function" && html`<option value="connect">${t("model_picker.connect_another", { count: picker.disconnected.length })}</option>`}
        </select></div>`}
    </div>`}
    ${help.picker}${help.connect}
    ${loading && html`<p role="status" class="muted small">${t("model_picker.checking_connections")}</p>`}
    ${error && html`<div class="ns-row"><p role="alert" class="message error">${errorText(error)}</p>
      <button type="button" class="btn secondary" ...${helpFor("chat.model_retry")} onClick=${onRetry}>${t("model_picker.retry")}</button></div>`}
    ${error && help.retry}
    ${!loading && !error && !picker.models.length && html`<p class="ns-picker-notice" role="status">${t("model_picker.connect_a_model_to_use")}</p>`}
    ${selected && html`<p class="muted small ns-model-facts">${selected.label} · ${selected.verificationLabel || t("model_picker.connected")} · ${t("model_picker.checked")}: ${selected.checkedAt ? fmtDate(selected.checkedAt) : t("model_picker.no_date")}
      ${selected.quota.map((line) => html`<span key=${line}> · ${line}</span>`)}
      ${selected.warning && html`<span class="ns-picker-notice"> · ${selected.warning}</span>`}
    </p>`}
    ${selected && help.choice}
    ${picker.disconnected.length > 0 && html`<details class="ns-picker-disconnected">
      <summary ...${helpFor("chat.model_disconnected")}>${t("model_picker.disconnected", { count: picker.disconnected.length })}</summary>
      ${help.disconnected}
      ${picker.disconnected.map((item) => html`<p key=${item.id} class="muted small">${item.label} · ${item.reason}
        ${item.quota.map((line) => html`<span key=${line}> · ${line}</span>`)}
        ${item.warning && html`<span> · ${item.warning}</span>`}
        ${item.agyNotice && html`<span> · ${item.agyNotice}</span>`}
      </p>`)}
    </details>`}
  </div>`;
}
