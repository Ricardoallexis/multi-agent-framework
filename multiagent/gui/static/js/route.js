// Copyright 2026 Ricardoallexis and contributors
// SPDX-License-Identifier: Apache-2.0
// Tiny hash router: "#sessions", "#sessions/<id>", "#tree/<session id>", "#runs", "#runs/<run id>" and "#new" (a new session:
// the guided chat of GUI2-T03, which can fall back to the create-run form). The id is URL-encoded.
// "#run=<id>" is what the API gives as the link of a framework session; it opens that run in "Ejecuciones".
// The screens that extensions register (extensions.js, FLU-T04) route the same way: "#<id>" and "#<id>/<item>".
// The local token travels in "#token=" only until main.js reads it and removes it from the address.
import { useState, useEffect } from "../vendor/htm-preact-standalone.module.js";
import { isRegisteredView, subscribeRegistry } from "./extensions.js";

export const DEFAULT_SECTION = "sessions";
const SECTION_IDS = ["sessions", "runs", "usage", "tree", "map"];   // extensions.js keeps RESERVED_SECTIONS equal to this plus "new"
export const NEW_SECTION = "new";

export function parseRoute(hash) {
  const text = String(hash || "").replace(/^#\/?/, "");
  if (text === NEW_SECTION) return { section: NEW_SECTION, id: null };
  const legacy = /^run=(.+)$/.exec(text);
  if (legacy) return { section: "runs", id: safeDecode(legacy[1]) };
  const slash = text.indexOf("/");
  const section = slash < 0 ? text : text.slice(0, slash);
  if (!SECTION_IDS.includes(section) && !isRegisteredView(section)) return { section: DEFAULT_SECTION, id: null };
  const id = slash < 0 ? "" : safeDecode(text.slice(slash + 1));
  return { section, id: id || null };
}

function safeDecode(value) {
  try { return decodeURIComponent(value); } catch (_) { return value; }
}

export function routeHref(section, id) {
  return "#" + section + (id ? "/" + encodeURIComponent(id) : "");
}

// Sets the address, which fires "hashchange" (so the Back button works too).
export function navigate(section, id) {
  const next = routeHref(section, id);
  if (location.hash === next) return;
  location.hash = next;
}

export function useRoute() {
  const [route, setRoute] = useState(() => parseRoute(location.hash));
  useEffect(() => {
    const update = () => setRoute(parseRoute(location.hash));
    window.addEventListener("hashchange", update);
    // An extension that registers its screen after the first render (it was slow) makes the address mean something new.
    const reparse = () => setRoute((previous) => {
      const next = parseRoute(location.hash);
      return next.section === previous.section && next.id === previous.id ? previous : next;
    });
    const off = subscribeRegistry(reparse);
    reparse();
    return () => { window.removeEventListener("hashchange", update); off(); };
  }, []);
  return route;
}
