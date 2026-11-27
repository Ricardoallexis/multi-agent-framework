// Copyright 2026 Ricardoallexis and contributors
// SPDX-License-Identifier: Apache-2.0
// Pure view data helpers for importing and exporting Offices; no DOM or network.
import { t } from "../../js/i18n.js";

export const CHANGE_TEXT = Object.freeze({
  added: "ext.offices.change.added",
  changed: "ext.offices.change.changed",
  removed: "ext.offices.change.removed",
  unchanged: "ext.offices.change.unchanged",
});
export const FILE_CHANGE_TEXT = Object.freeze({
  added: "ext.offices.file_change.added",
  changed: "ext.offices.file_change.changed",
  removed: "ext.offices.file_change.removed",
});

export const TEAMS_NOTE = () => t("ext.offices.teams_note");
export const ZIP_UPLOAD_MAX_BYTES = 25 * 2 ** 20;
export const REMOVE_NOTICE = () => t("ext.offices.remove_notice");

const REASON_TEXT = Object.freeze({
  office_has_code: "ext.offices.error.office_has_code",
  office_stage_expired: "ext.offices.error.office_stage_expired",
  office_changed_since_card: "ext.offices.error.office_changed_since_card",
  zip_too_large: "ext.offices.error.zip_too_large",
  bundle_busy: "ext.offices.error.bundle_busy",
  office_area_exists: "ext.offices.error.office_area_exists",
  office_install_not_approved: "ext.offices.error.office_install_not_approved",
  office_source_unavailable: "ext.offices.error.office_source_unavailable",
  office_source_changed: "ext.offices.error.office_source_changed",
  office_source_unchanged: "ext.offices.error.office_source_unchanged",
  risk_ack_required: "ext.offices.error.risk_ack_required",
  advanced_mode_required: "ext.offices.error.advanced_mode_required",
  zip_invalid: "ext.offices.error.zip_invalid",
  zip_unsafe_entry: "ext.offices.error.zip_unsafe_entry",
});

const SCAN_KEYS = Object.freeze({
  safe: "ext.offices.scan.safe",
  caution: "ext.offices.scan.caution",
  do_not_install: "ext.offices.scan.do_not_install",
  not_analyzed: "ext.offices.scan.not_analyzed",
  unscanned: "ext.offices.scan.unscanned",
  pending: "ext.offices.scan.pending",
  running: "ext.offices.scan.running",
  off: "ext.offices.scan.off",
});

const arrayOfStrings = (value) => Array.isArray(value)
  ? value.filter((item) => typeof item === "string")
  : [];
const scanText = (value) => typeof value === "string" && Object.prototype.hasOwnProperty.call(SCAN_KEYS, value)
  ? t(SCAN_KEYS[value])
  : "";

function areaLevel(area) {
  if (area?.local_only || area?.data_class === "restricted") {
    return t("ext.offices.level.local_only");
  }
  if (area?.sensitivity === "sensitive" || area?.data_class === "sensitive") return t("ext.offices.level.sensitive");
  if (area?.sensitivity === "normal" || area?.sensitivity === "non_sensitive" || area?.data_class === "non_sensitive") {
    return t("ext.offices.level.normal");
  }
  return t("ext.offices.level.none");
}

function levelDescription(level) {
  return level === "code" ? t("ext.offices.level.has_code") : t("ext.offices.level.instructions_only");
}

export function declarationName(value) {
  const title = String(value || "").replace(/([a-z0-9])([A-Z])/g, "$1 $2").replace(/[_-]+/g, " ").trim();
  return title ? title[0].toUpperCase() + title.slice(1) : "";
}

export function declaredFunctionsText(displayList, advanced) {
  const label = t(advanced ? "ext.offices.import.function_ids" : "ext.offices.import.declared_functions");
  return `${label}: ${displayList}`;
}

export function importTitle({ title, reimport, into }) {
  return title || (reimport ? t("ext.offices.reimport_title") : into
    ? t("ext.offices.add_area") : t("ext.offices.import.button"));
}

export function cardView(card) {
  const areas = Array.isArray(card?.areas) ? card.areas : [];
  const scan = card?.scan && typeof card.scan === "object" ? card.scan : {};
  const sourceCode = card?.code && typeof card.code === "object" ? card.code : {};
  const officeName = card?.office?.name || t("ext.offices.office.default");
  const scanAreas = scan.areas && typeof scan.areas === "object" ? scan.areas : {};
  const fileChanges = Array.isArray(card?.file_changes) ? card.file_changes
    .filter((item) => item && typeof item === "object" && typeof item.path === "string"
      && !item.path.startsWith("/") && !item.path.includes("\\") && !item.path.split("/").includes("..")
      && Object.prototype.hasOwnProperty.call(FILE_CHANGE_TEXT, item.change))
    .map((item) => ({
      area: typeof item.area === "string" ? item.area : "",
      path: item.path,
      change: t(FILE_CHANGE_TEXT[item.change]),
    })) : [];
  const scanUnavailable = scan.state === "not_installed"
    || Object.values(scanAreas).some((result) => result?.state === "not_installed");
  const areaNames = new Map(areas.map((area) => [String(area?.id || ""), String(area?.name || t("ext.offices.area.default"))]));
  return {
    title: String(officeName),
    notice: typeof card?.notice === "string" ? card.notice : "",
    level: levelDescription(card?.level),
    areas: areas.map((area) => {
      const declarations = area?.declares && typeof area.declares === "object" ? area.declares : {};
      const requirements = area?.requires && typeof area.requires === "object" ? area.requires : {};
      return {
        id: String(area?.id || ""),
        name: String(area?.name || t("ext.offices.area.default")),
        level: areaLevel(area),
        members: (Array.isArray(area?.members) ? area.members : []).filter((member) => member && typeof member === "object")
          .map((member) => {
            const name = typeof member.name === "string" ? member.name : "";
            const identifiers = [member.id, member.agent].filter((value) => typeof value === "string");
            const fallback = member.kind === "agent" ? t("ext.offices.member.default_agent") : t("ext.offices.member.default");
            return {
              name: name && !identifiers.includes(name) ? name : fallback,
              role: typeof member.role === "string" ? member.role : "",
              kind: typeof member.kind === "string" ? member.kind : "",
            };
          }),
        requirement: typeof requirements.text === "string" ? requirements.text : t("ext.offices.requirement.default"),
        declares: {
          workflows: arrayOfStrings(declarations.workflows),
          contracts: arrayOfStrings(declarations.contracts),
          roles: arrayOfStrings(declarations.roles),
        },
        workflowDeclarations: (Array.isArray(area?.workflow_declarations) ? area.workflow_declarations : [])
          .filter((item) => item && typeof item === "object")
          .map((item) => ({
            id: typeof item.id === "string" ? item.id : "",
            roles: arrayOfStrings(item.roles),
            fileTools: arrayOfStrings(item.file_tools),
          }))
          .filter((item) => item.id),
        change: CHANGE_TEXT[area?.change] ? t(CHANGE_TEXT[area.change]) : "",
      };
    }),
    fileChanges,
    fileToolsConfigured: card?.file_tools_configured === true,
    code: {
      present: sourceCode.present === true,
      files: arrayOfStrings(sourceCode.files),
      functions: arrayOfStrings(sourceCode.functions),
      functionsUnverified: sourceCode.functions_unverified === true,
      pythonContracts: arrayOfStrings(sourceCode.python_contracts),
      pythonContractsUnverified: sourceCode.python_contracts_unverified === true,
      scanUnavailable,
      verdict: scanUnavailable ? "" : scanText(typeof scan.verdict === "string" ? scan.verdict : scan.state),
      areas: Object.entries(scanAreas)
        .filter(([, result]) => result?.state !== "not_installed")
        .map(([id, result]) => ({
          id,
          name: areaNames.get(id) || t("ext.offices.area.default"),
          verdict: scanText(result?.verdict || result?.state) || t("ext.offices.scan.no_result"),
        })),
    },
    needsScanDecision: typeof scan.approval_id === "string" && scan.state === "pending",
    riskAckRequired: card?.risk_ack_required === true,
  };
}

export function errorText(reason, details = {}) {
  if (reason === "advanced_mode_required" && details?.action === "install_code") {
    return t(REASON_TEXT.office_has_code);
  }
  if (reason === "advanced_mode_required" && details?.action === "install_folder") {
    return t("ext.offices.error.install_folder_advanced");
  }
  if (REASON_TEXT[reason]) return t(REASON_TEXT[reason]);
  if (reason === "export_secret_found") {
    const findings = Array.isArray(details?.found) ? details.found : [];
    if (!findings.length) return t("ext.offices.error.export_secret");
    return findings.map((item) => {
      const path = typeof item?.path === "string" ? item.path : t("ext.offices.file.default");
      const line = Number.isInteger(item?.line) && item.line > 0 ? item.line : "?";
      return t("ext.offices.error.export_secret_at", { path, line });
    }).join(" ");
  }
  return t("ext.offices.error.default");
}

export function exportFileName(payload) {
  const supplied = typeof payload?.filename === "string" ? payload.filename : "";
  const name = supplied.replace(/\\/g, "/").split("/").pop()?.replace(/[\u0000-\u001f"]/g, "").trim();
  return name || t("ext.offices.export.filename");
}

export function exportDownloadedText(filename) {
  return t("ext.offices.export.downloaded", { filename });
}

export function includeCodeVisible(mode) {
  return mode === "advanced";
}

export function zipBody(filename, zipBase64, { into = null, reimport = null } = {}) {
  return {
    filename: String(filename || "").replace(/^.*[\\/]/, "").slice(0, 120),
    zip_base64: String(zipBase64 || ""),
    into: typeof into === "string" && into ? into : null,
    reimport: typeof reimport === "string" && reimport ? reimport : null,
  };
}

export function bytesToBase64(bytes) {
  let text = "";
  const view = bytes instanceof Uint8Array ? bytes : new Uint8Array(bytes);
  for (let at = 0; at < view.length; at += 0x8000) text += String.fromCharCode(...view.subarray(at, at + 0x8000));
  return btoa(text);
}

export function exportWarningText(warning) {
  if (warning?.code === "export_model_not_portable") {
    return t("ext.offices.export.model_not_portable");
  }
  if (warning?.code === "export_personal_data_found") {
    return t("ext.offices.export.personal_data");
  }
  return t("ext.offices.export.warning");
}
