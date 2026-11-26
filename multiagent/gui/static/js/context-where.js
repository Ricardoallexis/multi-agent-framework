// Copyright 2026 Ricardoallexis and contributors
// SPDX-License-Identifier: Apache-2.0
// Location details available from the subject already loaded by the right panel.
import { appLabel } from "./sidebar-model.js";
import { sourceLabel } from "./session-text.js";
import { t } from "./i18n.js";

export function contextWhere(subject, data) {
  if (!subject) return [];
  const app = subject.kind === "run" ? appLabel("workflow") : sourceLabel(String(subject.sessionId || "").split(":")[0]);
  const rows = [{ label: t("panel.where_app"), value: app }];
  const folder = subject.kind === "session" && data && data.summary && data.summary.cwd;
  if (typeof folder === "string" && folder.trim()) rows.push({ label: t("panel.work_folder"), value: folder.trim() });
  return rows;
}
