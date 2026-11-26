// Copyright 2026 Ricardoallexis and contributors
// SPDX-License-Identifier: Apache-2.0
// The limits chip of the sidebar footer (GUI4-T10): a mini bar for the app closest to its limit and a popover with one row per app.
// It only reads GET /gui/api/connections (cached, no spend); it never probes, never switches an app
// or a model, and never polls: it reads when it mounts and each time the popover opens. The logic is in limits-chip-model.js.
import { html, useEffect, useRef, useState } from "../vendor/htm-preact-standalone.module.js";
import { apiGet } from "./api.js";
import { loadToken } from "./auth.js";
import { Help, helpFor } from "./help.js";
import { t } from "./i18n.js";
import { appLimitRows, appName, chipRow, rowAria, rowDetail } from "./limits-chip-model.js";
import { percentText } from "./quota-text.js";
import { navigate } from "./route.js";
import { Icon } from "./ui/icon.js";

const helpEntry = () => ({ title: t("limits.help_title"), hint: t("limits.help_hint"), more: [t("limits.help_more_1"), t("limits.help_more_2")] });

function QuotaRow({ row }) {
  return html`<div class="quota-row">
    <div class="quota-title"><span>${appName(row.id)}</span><span>${t("limits.detail_used", { percent: percentText(row.used) })}</span></div>
    <progress class=${"limit-bar" + (row.limitReached ? " full" : "")} max="100" value=${row.used} aria-label=${rowAria(row)}>${percentText(row.used)}</progress>
    <p class="quota-detail">${rowDetail(row)}</p>
  </div>`;
}

export function LimitsChip() {
  // Shell does not receive the token, so it is read as pending.js does (main.js has already stored it).
  const [token] = useState(loadToken);
  const [data, setData] = useState({ rows: [], loaded: false, failed: false });
  const [open, setOpen] = useState(false);
  const box = useRef(null);
  const chip = useRef(null);
  const alive = useRef(true);
  const request = useRef(0);   // only the newest reading may set the rows
  useEffect(() => () => { alive.current = false; }, []);

  // Reads cached facts only; a failure leaves the last rows in place and says so in the popover.
  const read = () => {
    const mine = ++request.current;
    apiGet("/gui/api/connections", token).then(
      (payload) => { if (alive.current && mine === request.current) setData({ rows: appLimitRows(payload), loaded: true, failed: false }); },
      () => { if (alive.current && mine === request.current) setData((previous) => ({ ...previous, loaded: true, failed: true })); },
    );
  };
  useEffect(read, [token]);
  useEffect(() => { if (open) read(); }, [open]);

  // Esc and a click outside close the popover; Esc gives the focus back to the chip.
  useEffect(() => {
    if (!open) return undefined;
    const onKey = (event) => {
      if (event.key !== "Escape" || event.defaultPrevented) return;   // an open "?" help already took this Esc
      event.preventDefault();
      setOpen(false);
      if (chip.current) chip.current.focus();
    };
    const onPointer = (event) => { if (box.current && !box.current.contains(event.target)) setOpen(false); };
    document.addEventListener("keydown", onKey);
    document.addEventListener("pointerdown", onPointer);
    return () => { document.removeEventListener("keydown", onKey); document.removeEventListener("pointerdown", onPointer); };
  }, [open]);

  const top = chipRow(data.rows);
  const shown = data.rows;
  const seeAll = () => { setOpen(false); navigate("usage"); };
  const emptyText = !data.loaded ? t("limits.loading") : data.failed && !data.rows.length ? t("limits.error")
    : t("limits.none");

  return html`<div class="limits" ref=${box}>
    <button type="button" class="tool-btn limit-chip" ref=${chip} aria-expanded=${open ? "true" : "false"} aria-controls="limits-popover"
      ...${helpFor("limits.chip")} onClick=${() => setOpen(!open)}>
      <span class="limit-chip-label"><${Icon} name="chart" /><span class="limit-chip-title">${t("limits.title")}</span>
        ${top && html`<span class="limit-chip-value">${percentText(top.used)}</span>`}</span>
      ${top && html`<progress class=${"limit-bar mini" + (top.limitReached ? " full" : "")} max="100" value=${top.used} aria-label=${rowAria(top)}>${percentText(top.used)}</progress>`}
    </button>
    <${Help} topic="limits.chip" entry=${helpEntry()} compact=${true} />
    <section class="limits-popover popover-surface" id="limits-popover" aria-label=${t("limits.title")} hidden=${!open}>
      <div class="popover-head"><strong>${t("limits.title")}</strong>
        <button type="button" class="tool-btn icon-only" data-help-exempt="navigation" aria-label=${t("limits.close")} onClick=${() => { setOpen(false); if (chip.current) chip.current.focus(); }}><${Icon} name="close" /></button></div>
      ${shown.length ? shown.map((row) => html`<${QuotaRow} key=${row.id} row=${row} />`) : html`<p class="quota-detail" role="status">${emptyText}</p>`}
      ${data.failed && data.rows.length > 0 && html`<p class="quota-detail" role="status">${t("limits.error")}</p>`}
      <button type="button" class="btn text" data-help-exempt="navigation" onClick=${seeAll}>${t("limits.view_all")}</button>
    </section>
  </div>`;
}
