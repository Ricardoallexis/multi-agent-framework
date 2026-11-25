// Copyright 2026 Ricardoallexis and contributors
// SPDX-License-Identifier: Apache-2.0

export const LEVEL_OPTIONS = [
  { value: "normal", label: "Normal" },
  { value: "sensitive", label: "Sensible" },
  { value: "local_only", label: "Solo local" },
];

export const NOTE_TITLE = "Lo que esta opción no libera";

export const QUESTION_LINES = {
  normal: "Normal: puede usar modelos de la nube si lo permites en «Privacidad»",
  sensitive: "Sensible: solo agentes de tu equipo",
  local_only: "Solo local: nunca sale de tu equipo",
};

const LEVEL_RANK = { normal: 0, sensitive: 1, local_only: 2 };

export function isLowering(current, chosen) {
  if (chosen === null || chosen === undefined) return current !== null && current !== undefined;
  return Object.prototype.hasOwnProperty.call(LEVEL_RANK, current)
    && Object.prototype.hasOwnProperty.call(LEVEL_RANK, chosen)
    && LEVEL_RANK[chosen] < LEVEL_RANK[current];
}

export function privacyRows(payload, areas = []) {
  const names = new Map((Array.isArray(areas) ? areas : []).map((area) => [area.id, area.name || area.id]));
  return (Array.isArray(payload?.areas) ? payload.areas : []).map((area) => {
    const areaCloud = area.cloud || {};
    const cloudFromArea = areaCloud.source !== "default";
    const cloudRecord = cloudFromArea ? areaCloud : payload?.cloud || {};
    const cloudSourceText = cloudFromArea ? "de esta área"
      : cloudRecord.source !== "default" ? "de la oficina" : "por defecto";
    return {
      id: area.id,
      name: names.get(area.id) || area.name || area.id,
      level: area.local_only && !payload.local_only ? "local_only" : area.sensitivity || null,
      setBy: area.set_by || null,
      setAt: area.set_at || null,
      sourceText: area.source === "person" ? "de esta área" : area.source === "office" ? "de la oficina" : "sin fijar",
      dataClass: area.data_class || null,
      cloudEffective: area.cloud_effective === true,
      cloudSetBy: cloudRecord.set_by || null,
      cloudSetAt: cloudRecord.set_at || null,
      cloudSourceText,
    };
  });
}

export function levelBody(area, chosen) {
  if (!LEVEL_OPTIONS.some((option) => option.value === chosen)) {
    throw new TypeError("Choose a supported office privacy level.");
  }
  return {
    area: area || null,
    sensitivity: chosen === "local_only" ? null : chosen,
    local_only: chosen === "local_only",
  };
}

export async function askLevelOnce({ token, officeId, area, retry, post, isStale = () => false }) {
  if (typeof retry !== "function") throw new TypeError("An office privacy retry callback is required.");
  if (typeof post !== "function") throw new TypeError("An office privacy POST callback is required.");
  const chosen = await retry({ options: LEVEL_OPTIONS, lines: QUESTION_LINES });
  if (isStale() || chosen === null || chosen === undefined) return null;
  const path = "/gui/api/offices/" + encodeURIComponent(officeId) + "/privacy/raise";
  await post(path, token, levelBody(area, chosen));
  if (isStale()) return null;
  const result = await retry();
  if (isStale()) return null;
  return result;
}
