// Copyright 2026 Ricardoallexis and contributors
// SPDX-License-Identifier: Apache-2.0
import { t } from "./i18n.js";
// AVA-T05: pure rules for messages sent to the person's inbox.

export const MESSAGE_KINDS = ["cancel", "interrupt", "adjust", "note"];
export const NEEDS_CONFIRMATION = ["cancel", "interrupt", "adjust"];

export function messageError(kind, text, enabled, reason = "") {
  if (!enabled) return reason || t("pending.availability_off");
  if (!MESSAGE_KINDS.includes(kind)) return t("pending.invalid_action");
  if ((kind === "adjust" || kind === "note") && !String(text || "").trim()) return t("pending.write_message");
  if (new TextEncoder().encode(String(text || "").trim()).length > 1200) return t("pending.message_too_long");
  return "";
}

export function messageId() {
  const bytes = new Uint8Array(16);
  crypto.getRandomValues(bytes);
  return "gui-" + Array.from(bytes, (byte) => byte.toString(16).padStart(2, "0")).join("");
}

export function messagePath(scope, id) {
  if (!(["runs", "tasks"].includes(scope))) throw new Error("Invalid inbox scope");
  return "/gui/api/" + scope + "/" + encodeURIComponent(id) + "/messages";
}
