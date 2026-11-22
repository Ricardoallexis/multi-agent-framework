// Copyright 2026 Ricardoallexis and contributors
// SPDX-License-Identifier: Apache-2.0
// Tiny hash router: "#sessions", "#sessions/<id>", "#tree/<session id>", "#runs", "#runs/<run id>", "#evaluation/<set name>", "#teams/<bundle>", "#plans/<id>" and "#new" (a new session:
// the guided chat of GUI2-T03, which can fall back to the create-run form). The id is URL-encoded.
// "#run=<id>" is what the API gives as the link of a framework session; it opens that run in "Ejecuciones".
// The screens that extensions register (extensions.js, FLU-T04) route the same way: "#<id>" and "#<id>/<item>".
// The local token travels in "#token=" only until main.js reads it and removes it from the address.
import { useState, useEffect } from "../vendor/htm-preact-standalone.module.js";
import { isRegisteredView, subscribeRegistry } from "./extensions.js";

export const DEFAULT_SECTION = "sessions";
const SECTION_IDS = ["sessions", "runs", "usage", "evaluation", "teams", "plans", "tree", "map", "pending", "customize", "packages"];   // extensions.js keeps RESERVED_SECTIONS equal to this plus "new"
export const NEW_SECTION = "new";

export function parseRoute(hash) {
  const text = String(hash || "").replace(/^#\/?/, "");
  if (text === NEW_SECTION) return { section: NEW_SECTION, id: null };
  // CHT-T05: "#new/team/<version id>" is a team version as a one-stop thread of the chat; main.js mounts team-thread.js for it.
  const newTeam = /^new\/team\/(.+)$/.exec(text);
  if (newTeam) return { section: NEW_SECTION, id: safeDecode(newTeam[1]), kind: "team" };
  // CHT-T06: "#new/plan/<proposal id>" is a plan proposal as a thread of the chat; main.js mounts plan-thread.js for it.
  const newPlan = /^new\/plan\/(.+)$/.exec(text);
  if (newPlan) return { section: NEW_SECTION, id: safeDecode(newPlan[1]), kind: "plan" };
  const newRun = /^new\/run\/(.+)$/.exec(text);
  if (newRun) return { section: NEW_SECTION, id: safeDecode(newRun[1]) };
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

export const threadHref = (runId) => "#new/run/" + encodeURIComponent(runId);
export const teamThreadHref = (versionId) => "#new/team/" + encodeURIComponent(versionId);   // CHT-T05
export const planThreadHref = (proposalId) => "#new/plan/" + encodeURIComponent(proposalId);   // CHT-T06

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
