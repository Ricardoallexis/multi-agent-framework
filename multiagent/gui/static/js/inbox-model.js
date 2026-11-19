// Copyright 2026 Ricardoallexis and contributors
// SPDX-License-Identifier: Apache-2.0
// AVA-T05: pure rules for messages sent to the person's inbox.

export const MESSAGE_KINDS = ["cancel", "interrupt", "adjust", "note"];
export const NEEDS_CONFIRMATION = ["cancel", "interrupt", "adjust"];

export function messageError(kind, text, enabled, reason = "") {
  if (!enabled) return reason || "La política de disponibilidad está apagada.";
  if (!MESSAGE_KINDS.includes(kind)) return "Elige una acción válida.";
  if ((kind === "adjust" || kind === "note") && !String(text || "").trim()) return "Escribe un mensaje.";
  if (new TextEncoder().encode(String(text || "").trim()).length > 1200) return "El mensaje supera 1200 bytes.";
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
