"use client";
import { Suspense, useState } from "react";
import { useSearchParams } from "next/navigation";

// Only same-origin paths: the URL parser (not a prefix check) decides, so "//x", "/\\x", "/\t/x" fall back to "/".
function safeNext(n: string | null) {
  if (!n?.startsWith("/")) return "/";
  try {
    const u = new URL(n, window.location.origin);
    return u.origin === window.location.origin ? u.pathname + u.search + u.hash : "/";
  } catch { return "/"; }
}

function Form() {
  const next = safeNext(useSearchParams().get("next"));
  const [pw, setPw] = useState("");
  const [err, setErr] = useState("");
  const [busy, setBusy] = useState(false);

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    setBusy(true); setErr("");
    try {
      const r = await fetch("/auth/unlock", {
        method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ passphrase: pw }),
      });
      if (r.ok) return window.location.assign(next);
      const d = await r.json().catch(() => null);
      setErr(typeof d?.detail === "string" ? d.detail : r.status === 429 ? "Too many attempts. Wait and retry." : "Wrong passphrase.");
    } catch {
      setErr("Could not reach the server.");
    }
    setBusy(false);
  }

  return (
    <form onSubmit={submit}>
      <div className="field">
        <label htmlFor="pass">Passphrase</label>
        <input id="pass" type="password" autoComplete="current-password" value={pw} onChange={(e) => setPw(e.target.value)} required />
      </div>
      {err && <p className="err" role="alert">{err}</p>}
      <button className="btn primary" type="submit" disabled={busy}>Unlock</button>
    </form>
  );
}

export default function Unlock() {
  return (
    <section className="hero">
      <p className="eyebrow">Private demo</p>
      <h1>Enter the <em>passphrase</em></h1>
      <p className="lede">This demo is gated. Ask the team for the passphrase.</p>
      <Suspense fallback={null}><Form /></Suspense>
    </section>
  );
}
