// Copyright 2026 Ricardoallexis and contributors
// SPDX-License-Identifier: Apache-2.0
// Pure view data helpers for Offices; no DOM, storage, or network access.
import { t } from "../../js/i18n.js";

export const POLL_MS = 4000;
export const LEVEL_TEXT = Object.freeze({
  get non_sensitive() { return t("ext.offices.level.normal"); },
  get sensitive() { return t("ext.offices.level.sensitive"); },
  get restricted() { return t("ext.offices.level.local_only"); },
  get none() { return t("ext.offices.level.none"); },
});
export const EMPTY_TEXT = () => t("ext.offices.empty");
export const OFFLINE_TEXT = () => t("ext.offices.offline");

const PERMISSION_TEXT = Object.freeze({
  read: () => t("ext.offices.permission.read"),
  write: () => t("ext.offices.permission.write"),
  destructive: () => t("ext.offices.permission.destructive"),
  external: () => t("ext.offices.permission.external"),
});

const DEGRADED_TEXT = Object.freeze({
  team_not_approved: "ext.offices.team_not_approved",
  requirement_missing: "ext.offices.degraded.requirement_missing",
  installed_bundle_changed: "ext.offices.degraded.bundle_changed",
  bundle_not_installed: "ext.offices.degraded.bundle_missing",
});

export function levelText(value) {
  return LEVEL_TEXT[value || "none"] || LEVEL_TEXT.none;
}

export function memberLine(member) {
  if (member?.kind === "person") return t("ext.offices.member.person", { name: member.name || t("ext.offices.member.default_person") });
  const worker = member?.worker && member.worker !== "—"
    ? member.worker
    : member?.agent || member?.name || t("ext.offices.member.default_agent");
  return t("ext.offices.member.worker", { model: worker });
}

export function declaredPermissionText(team) {
  const values = [...new Set((Array.isArray(team?.roles) ? team.roles : [])
    .flatMap((role) => Array.isArray(role?.permissions) ? role.permissions : []))];
  if (!values.length) return "";
  return t("ext.offices.permissions", { permissions: values.map((value) => PERMISSION_TEXT[value]?.() || value).join(", ") });
}

export function degradedAction(entry) {
  const fix = entry?.fix;
  if (!fix || typeof fix.label !== "string") return { label: "", href: null, action: null };
  return {
    label: fix.label,
    href: typeof fix.href === "string" && fix.href ? fix.href : null,
    action: typeof fix.action === "string" && fix.action ? fix.action : null,
  };
}

export function degradedMessage(code) {
  return t(DEGRADED_TEXT[code] || "ext.offices.degraded.default");
}

export function sceneTargets(offices) {
  if (!Array.isArray(offices)) return [];
  return offices.map((office) => office?.id).filter((id) => typeof id === "string" && id.length > 0);
}

// The header says «Lista» only when the office itself is fine and no area of the scene is degraded (a team waiting
// for its approval counts; OfficeServices.get only counts the packages).
export function officeWorks(office, scene) {
  if (office?.state && office.state !== "ok") return false;
  const areas = Array.isArray(scene?.areas) ? scene.areas : [];
  return !areas.some((area) => Array.isArray(area?.degraded) && area.degraded.length > 0);
}

export function officeRows(scene, requirements) {
  if (!scene) return [];
  const coverage = new Map((Array.isArray(requirements?.areas) ? requirements.areas : [])
    .filter((area) => typeof area?.id === "string")
    .map((area) => [area.id, area]));
  const orchestrator = scene.orchestrator || {};
  const rows = [{
    key: "coordinator",
    kind: "orchestrator",
    name: t("ext.offices.coordination"),
    label: orchestrator.label || t("ext.offices.member.free"),
    state: orchestrator.state || "idle",
    plan: orchestrator.plan || null,
  }];
  for (const area of Array.isArray(scene.areas) ? scene.areas : []) {
    const requirement = coverage.get(area.id);
    rows.push({
      key: "area:" + area.id,
      kind: "area",
      id: area.id,
      name: area.name || t("ext.offices.area.default"),
      label: area.label || t("ext.offices.member.free"),
      state: area.state || "idle",
      level: levelText(area.data_class),
      requirementText: typeof requirement?.text === "string" ? requirement.text : "",
      degraded: Array.isArray(area.degraded) ? area.degraded : [],
      permissions: declaredPermissionText(area.team),
    });
    for (const member of Array.isArray(area.members) ? area.members : []) {
      rows.push({
        key: "member:" + area.id + ":" + member.id,
        kind: "member",
        name: member.name || t("ext.offices.member.default"),
        line: memberLine(member),
        label: member.label || t("ext.offices.member.free"),
        state: member.state || "idle",
      });
    }
  }
  return rows;
}

export function createScenePoller({
  fetchScene,
  onScene,
  onFailure,
  isVisible = () => true,
  setIntervalFn = setInterval,
  clearIntervalFn = clearInterval,
}) {
  let active = false;
  let inFlight = false;
  let timer = null;

  const refresh = async () => {
    if (!active || inFlight) return;
    inFlight = true;
    let scene;
    try {
      scene = await fetchScene();
    } catch (error) {
      if (active) onFailure(error);
      return;
    } finally {
      inFlight = false;
    }
    if (active) onScene(scene);
  };

  return {
    start() {
      if (active) return Promise.resolve();
      active = true;
      timer = setIntervalFn(() => (isVisible() ? refresh() : undefined), POLL_MS);
      return refresh();
    },
    stop() {
      if (!active) return;
      active = false;
      if (timer !== null) clearIntervalFn(timer);
      timer = null;
    },
    refresh,
  };
}
