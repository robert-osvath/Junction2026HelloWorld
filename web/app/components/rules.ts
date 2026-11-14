"use client";
import { useSyncExternalStore } from "react";

// The buyer rules the Vimo cockpit (public/vimo/app.js) saves under "aimo-rules". Same origin, same shape,
// so rules saved here are the ones Vimo uses and vice versa.
export type SavedRules = {
  buyer?: string; buyer_type?: string; limit?: string; budget?: string; claim?: string; allergens?: string;
  substitutes?: string; strict_allergens?: string; policy_id?: string;
};

const KEY = "aimo-rules";
const subscribe = (cb: () => void) => { window.addEventListener("storage", cb); return () => window.removeEventListener("storage", cb); };
const read = () => { try { return localStorage.getItem(KEY) ?? ""; } catch { return ""; } };

export function useSavedRules(): SavedRules {
  const raw = useSyncExternalStore(subscribe, read, () => "");
  try { return raw ? JSON.parse(raw) : {}; } catch { return {}; }
}

export function saveRules(rules: SavedRules) {
  try { localStorage.setItem(KEY, JSON.stringify(rules)); } catch { /* private mode: rules still apply server-side */ }
  window.dispatchEvent(new StorageEvent("storage", { key: KEY }));  // the "storage" event only fires in other tabs
}
