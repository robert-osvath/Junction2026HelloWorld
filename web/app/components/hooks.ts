"use client";
import { useEffect, useReducer, useState, useSyncExternalStore } from "react";
import { api } from "../api";

type Polled<T> = { path: string; data: T | null; err: string };

// GET a path and refresh it every `ms`; reload() after a write so the page shows the new state at once.
// Results are tagged with their path: a slow response for a previous path never shows under the new one.
export function usePoll<T>(path: string, ms = 10_000) {
  const [state, setState] = useState<Polled<T>>({ path, data: null, err: "" });
  const [tick, reload] = useReducer((n: number) => n + 1, 0);
  useEffect(() => {
    let live = true;
    const load = () => api<T>(path)
      .then((data) => live && setState({ path, data, err: "" }))
      .catch((e: Error) => live && setState((s) => ({ path, data: s.path === path ? s.data : null, err: e.message })));
    void load();
    const t = setInterval(load, ms);
    return () => { live = false; clearInterval(t); };
  }, [path, ms, tick]);
  const cur = state.path === path ? state : { data: null, err: "" };
  return { data: cur.data, err: cur.err, loading: cur.data === null && !cur.err, reload };
}

// Current time rounded to the minute; 0 while prerendering, so dates never bake in the build time.
const subscribe = (cb: () => void) => { const t = setInterval(cb, 30_000); return () => clearInterval(t); };
const minute = () => Math.floor(Date.now() / 60_000) * 60_000;
export const useNow = () => useSyncExternalStore(subscribe, minute, () => 0);

export function useToast() {
  const [toast, setToast] = useState({ text: "", n: 0 });
  useEffect(() => {
    if (!toast.text) return;
    const t = setTimeout(() => setToast((s) => ({ ...s, text: "" })), 4000);
    return () => clearTimeout(t);
  }, [toast]);
  return [toast.text, (text: string) => setToast((s) => ({ text, n: s.n + 1 }))] as const;
}
