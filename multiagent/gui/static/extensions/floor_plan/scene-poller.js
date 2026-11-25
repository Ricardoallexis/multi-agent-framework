// Copyright 2026 Ricardoallexis and contributors
// SPDX-License-Identifier: Apache-2.0
// The floor plan's own poll of GET /gui/api/offices/{id}/scene (GUI4-T07). One request at a time; a hidden tab waits.
// An extension cannot import another one, so this is a small copy of the idea in the offices extension.

export const POLL_MS = 4000;

export function createScenePoller({
  fetchScene,
  onScene,
  onFailure,
  isVisible = () => true,
  setIntervalFn = setInterval,
  clearIntervalFn = clearInterval,
  intervalMs = POLL_MS,
}) {
  let active = false;
  let inFlight = false;
  let timer = null;

  const refresh = async () => {
    if (!active || inFlight) return;
    inFlight = true;
    let scene;
    try {
      scene = await fetchScene();
    } catch (error) {
      if (active) onFailure(error);
      return;
    } finally {
      inFlight = false;
    }
    if (active) onScene(scene);
  };

  return {
    start() {
      if (active) return Promise.resolve();
      active = true;
      timer = setIntervalFn(() => (isVisible() ? refresh() : undefined), intervalMs);
      return refresh();
    },
    stop() {
      if (!active) return;
      active = false;
      if (timer !== null) clearIntervalFn(timer);
      timer = null;
    },
    // A tab that becomes visible asks at once instead of waiting for the next tick.
    refresh() { return isVisible() ? refresh() : Promise.resolve(); },
  };
}
