// Copyright 2026 Ricardoallexis and contributors
// SPDX-License-Identifier: Apache-2.0
// Reads one GET route of /gui/api now and then (CNT-T06). The answer is kept while a later read fails, so a screen never empties
// because the server was busy for a moment; `error` says the last read failed. A null path reads nothing.
import { useEffect, useRef, useState } from "../../vendor/htm-preact-standalone.module.js";
import { ApiError, apiGet } from "../../js/api.js";

export const POLL_MS = 20000;
export const POLICY_POLL_MS = 60000;

export function usePolled(path, token, interval = POLL_MS) {
  const [state, setState] = useState({ loading: Boolean(path), data: null, error: null, refreshing: false });
  const ticket = useRef(0);
  const alive = useRef(true);

  async function reload() {
    if (!path) return;
    const mine = ++ticket.current;
    setState((previous) => ({ ...previous, refreshing: true }));
    try {
      const data = await apiGet(path, token);
      if (alive.current && mine === ticket.current) setState({ loading: false, data, error: null, refreshing: false });
    } catch (failure) {
      const error = failure instanceof ApiError ? failure : new ApiError({ message: String(failure) });
      if (alive.current && mine === ticket.current) setState((previous) => ({ ...previous, loading: false, error, refreshing: false }));
    }
  }

  useEffect(() => {
    alive.current = true;
    setState({ loading: Boolean(path), data: null, error: null, refreshing: false });
    if (!path) return undefined;
    reload();
    const timer = setInterval(reload, interval);
    return () => { alive.current = false; ticket.current += 1; clearInterval(timer); };
  }, [path, token]);

  return { ...state, reload };
}
