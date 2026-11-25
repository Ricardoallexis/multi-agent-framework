// Copyright 2026 Ricardoallexis and contributors
// SPDX-License-Identifier: Apache-2.0
// Pure view data helpers for importing and exporting Offices; no DOM or network.

export const CHANGE_TEXT = Object.freeze({
  added: "Nueva",
  changed: "Cambió",
  removed: "Se quita",
  unchanged: "Sin cambios",
});
export const FILE_CHANGE_TEXT = Object.freeze({
  added: "Añadido",
  changed: "Cambiado",
  removed: "Quitado",
});

export const TEAMS_NOTE = "Al instalar se aprueban los equipos de estas áreas";
export const ZIP_UPLOAD_MAX_BYTES = 25 * 2 ** 20;
export const REMOVE_NOTICE = "Se quita de la lista. Se conservan sus datos";

const REASON_TEXT = Object.freeze({
  office_has_code: "Esta oficina trae código: impórtala en modo avanzado.",
  office_stage_expired: "La ficha caducó: vuelve a elegir el archivo.",
  office_changed_since_card: "La oficina cambió desde que la viste: vuelve a elegir el archivo.",
  zip_too_large: "El archivo pasa de 25 MB.",
  bundle_busy: "Espera a que terminen sus trabajos.",
  office_area_exists: "Esta oficina ya tiene un área con ese nombre.",
  office_install_not_approved: "Aprueba la instalación de esta ficha antes de continuar.",
  office_source_unavailable: "La carpeta de origen no está disponible. No se instaló ningún cambio.",
  office_source_changed: "La carpeta de origen cambió desde que revisaste la ficha. Revisa de nuevo la actualización.",
  office_source_unchanged: "La oficina no tiene cambios para actualizar.",
  risk_ack_required: "Marca «Asumo los riesgos» para instalar esta oficina.",
  advanced_mode_required: "Esta acción necesita el modo avanzado.",
  zip_invalid: "El archivo no es un .zip válido.",
  zip_unsafe_entry: "El archivo contiene una ruta que no se permite; no se instaló nada.",
});

const SCAN_TEXT = Object.freeze({
  safe: "Sin alertas conocidas",
  caution: "Precaución",
  do_not_install: "Se encontraron riesgos",
  not_analyzed: "No se pudo completar la revisión",
  unscanned: "Sin revisar",
  pending: "Falta permitir la consulta",
  running: "Revisión en curso",
  off: "Revisión desactivada",
});

const arrayOfStrings = (value) => Array.isArray(value)
  ? value.filter((item) => typeof item === "string")
  : [];
const scanText = (value) => typeof value === "string" && Object.prototype.hasOwnProperty.call(SCAN_TEXT, value)
  ? SCAN_TEXT[value]
  : "";

function areaLevel(area) {
  if (area?.local_only || area?.data_class === "restricted") {
    return "Solo local";
  }
  if (area?.sensitivity === "sensitive" || area?.data_class === "sensitive") return "Sensible";
  if (area?.sensitivity === "normal" || area?.sensitivity === "non_sensitive" || area?.data_class === "non_sensitive") {
    return "Normal";
  }
  return "Sin nivel";
}

function levelDescription(level) {
  return level === "code" ? "Trae código propio" : "Solo instrucciones y datos";
}

export function declarationName(value) {
  const title = String(value || "").replace(/([a-z0-9])([A-Z])/g, "$1 $2").replace(/[_-]+/g, " ").trim();
  return title ? title[0].toUpperCase() + title.slice(1) : "";
}

export function cardView(card) {
  const areas = Array.isArray(card?.areas) ? card.areas : [];
  const scan = card?.scan && typeof card.scan === "object" ? card.scan : {};
  const sourceCode = card?.code && typeof card.code === "object" ? card.code : {};
  const officeName = card?.office?.name || "Oficina";
  const scanAreas = scan.areas && typeof scan.areas === "object" ? scan.areas : {};
  const fileChanges = Array.isArray(card?.file_changes) ? card.file_changes
    .filter((item) => item && typeof item === "object" && typeof item.path === "string"
      && !item.path.startsWith("/") && !item.path.includes("\\") && !item.path.split("/").includes("..")
      && Object.prototype.hasOwnProperty.call(FILE_CHANGE_TEXT, item.change))
    .map((item) => ({
      area: typeof item.area === "string" ? item.area : "",
      path: item.path,
      change: FILE_CHANGE_TEXT[item.change],
    })) : [];
  const scanUnavailable = scan.state === "not_installed"
    || Object.values(scanAreas).some((result) => result?.state === "not_installed");
  const areaNames = new Map(areas.map((area) => [String(area?.id || ""), String(area?.name || "Área")]));
  return {
    title: String(officeName),
    notice: typeof card?.notice === "string" ? card.notice : "",
    level: levelDescription(card?.level),
    areas: areas.map((area) => {
      const declarations = area?.declares && typeof area.declares === "object" ? area.declares : {};
      const requirements = area?.requires && typeof area.requires === "object" ? area.requires : {};
      return {
        id: String(area?.id || ""),
        name: String(area?.name || "Área"),
        level: areaLevel(area),
        members: (Array.isArray(area?.members) ? area.members : []).filter((member) => member && typeof member === "object")
          .map((member) => {
            const name = typeof member.name === "string" ? member.name : "";
            const identifiers = [member.id, member.agent].filter((value) => typeof value === "string");
            const fallback = member.kind === "agent" ? "Agente" : "Miembro";
            return {
              name: name && !identifiers.includes(name) ? name : fallback,
              role: typeof member.role === "string" ? member.role : "",
              kind: typeof member.kind === "string" ? member.kind : "",
            };
          }),
        requirement: typeof requirements.text === "string" ? requirements.text : "No se indicó qué necesita esta área.",
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
        change: CHANGE_TEXT[area?.change] || "",
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
          name: areaNames.get(id) || "Área",
          verdict: scanText(result?.verdict || result?.state) || "Sin resultado",
        })),
    },
    needsScanDecision: typeof scan.approval_id === "string" && scan.state === "pending",
    riskAckRequired: card?.risk_ack_required === true,
  };
}

export function errorText(reason, details = {}) {
  if (reason === "advanced_mode_required" && details?.action === "install_code") {
    return REASON_TEXT.office_has_code;
  }
  if (reason === "advanced_mode_required" && details?.action === "install_folder") {
    return "La ruta local de una carpeta necesita el modo avanzado.";
  }
  if (REASON_TEXT[reason]) return REASON_TEXT[reason];
  if (reason === "export_secret_found") {
    const findings = Array.isArray(details?.found) ? details.found : [];
    if (!findings.length) return "Hay algo que parece una clave. Quítalo y vuelve a exportar.";
    return findings.map((item) => {
      const path = typeof item?.path === "string" ? item.path : "un archivo";
      const line = Number.isInteger(item?.line) && item.line > 0 ? item.line : "?";
      return `Hay algo que parece una clave en ${path}, línea ${line}. Quítalo y vuelve a exportar.`;
    }).join(" ");
  }
  return "No se pudo completar. Revisa la oficina o el archivo e inténtalo de nuevo.";
}

export function exportFileName(payload) {
  const supplied = typeof payload?.filename === "string" ? payload.filename : "";
  const name = supplied.replace(/\\/g, "/").split("/").pop()?.replace(/[\u0000-\u001f"]/g, "").trim();
  return name || "oficina.zip";
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
    return "Un modelo de esta oficina puede no estar disponible en otra computadora.";
  }
  return "La oficina incluye un aviso de exportación.";
}
