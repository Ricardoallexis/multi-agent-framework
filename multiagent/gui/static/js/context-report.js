// Copyright 2026 Ricardoallexis and contributors
// SPDX-License-Identifier: Apache-2.0
// For a slot of the «Contexto» panel (GUI3-T04): tells the panel whether the card has content. The panel hands every slot an
// onContent prop; a slot calls this hook with { has, urgent } and the panel draws, or hides, the card. A slot that does not
// call it counts as «with content» (context-model.js).
import { useEffect } from "../vendor/htm-preact-standalone.module.js";

export function useContentReport(onContent, has, urgent = false) {
  useEffect(() => {
    if (typeof onContent === "function") onContent({ has: Boolean(has), urgent: Boolean(urgent) });
  }, [onContent, Boolean(has), Boolean(urgent)]);
}

// The same report as a component that draws nothing, for a branch whose own component has no hooks (an empty state).
export function ContentReport({ onContent, has = false, urgent = false }) {
  useContentReport(onContent, has, urgent);
  return null;
}
