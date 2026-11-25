// Copyright 2026 Ricardoallexis and contributors
// SPDX-License-Identifier: Apache-2.0
import { t } from "./i18n.js";

export const LEVEL_OPTIONS = [
  { value: "normal", get label() { return t("office_level.normal"); } },
  { value: "sensitive", get label() { return t("office_level.sensitive"); } },
  { value: "local_only", get label() { return t("office_level.local_only"); } },
];

export const NOTE_TITLE = () => t("office_level.note_title");

export const QUESTION_LINES = {
  get normal() { return t("office_level.question.normal"); },
  get sensitive() { return t("office_level.question.sensitive"); },
  get local_only() { return t("office_level.question.local_only"); },
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
    const cloudSourceText = cloudFromArea ? t("office_level.source.area")
      : cloudRecord.source !== "default" ? t("office_level.source.office") : t("office_level.source.default");
    return {
      id: area.id,
      name: names.get(area.id) || area.name || area.id,
      level: area.local_only && !payload.local_only ? "local_only" : area.sensitivity || null,
      setBy: area.set_by || null,
      setAt: area.set_at || null,
      sourceText: area.source === "person" ? t("office_level.source.area")
        : area.source === "office" ? t("office_level.source.office") : t("office_level.source.unset"),
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
