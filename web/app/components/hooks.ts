"use client";
import { useCallback, useEffect, useState, useSyncExternalStore } from "react";
import { api } from "../api";

// GET a path and refresh it every `ms`; reload() after a write so the page shows the new state at once.
export function usePoll<T>(path: string, ms = 10_000) {
  const [data, setData] = useState<T | null>(null);
  const [err, setErr] = useState("");
  const reload = useCallback(
    () => api<T>(path).then((d) => { setData(d); setErr(""); }).catch((e: Error) => setErr(e.message)),
    [path],
  );
  useEffect(() => {
    void reload();
    const t = setInterval(reload, ms);
    return () => clearInterval(t);
  }, [reload, ms]);
  return { data, err, reload };
}

// Current time rounded to the minute; 0 while prerendering, so dates never bake in the build time.
const tick = (cb: () => void) => { const t = setInterval(cb, 30_000); return () => clearInterval(t); };
const minute = () => Math.floor(Date.now() / 60_000) * 60_000;
export const useNow = () => useSyncExternalStore(tick, minute, () => 0);

export function useToast() {
  const [toast, setToast] = useState("");
  useEffect(() => {
    if (!toast) return;
    const t = setTimeout(() => setToast(""), 4000);
    return () => clearTimeout(t);
  }, [toast]);
  return [toast, setToast] as const;
}
