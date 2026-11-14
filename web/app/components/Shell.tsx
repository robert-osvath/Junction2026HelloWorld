"use client";
import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { Suspense, useEffect, useState, useSyncExternalStore, type ReactNode } from "react";
import { api, type Order, type Status } from "../api";
import Icon, { type IconName } from "./Icon";

type Nav = { href: string; label: string; icon: IconName; count?: Status };
export const NAV: Nav[] = [
  { href: "/", label: "Overview", icon: "home" },
  { href: "/approvals", label: "Approval inbox", icon: "check", count: "pending_approval" },
];

// Same-origin with /vimo, so the buyer rules Vimo saved there are readable here.
const RULES_KEY = "aimo-rules";
const subscribe = (cb: () => void) => { window.addEventListener("storage", cb); return () => window.removeEventListener("storage", cb); };
const readRules = () => { try { return localStorage.getItem(RULES_KEY) ?? ""; } catch { return ""; } };
export function useSavedRules(): { buyer?: string; policy_id?: string } {
  const raw = useSyncExternalStore(subscribe, readRules, () => "");
  try { return raw ? JSON.parse(raw) : {}; } catch { return {}; }
}

const initials = (name: string) => name.split(/\s+/).filter(Boolean).slice(0, 2).map((w) => w[0]!.toUpperCase()).join("") || "?";
const isActive = (path: string | null, href: string) => path !== null && (href === "/" ? path === "/" : path === href || path.startsWith(`${href}/`));

function NavLinks({ path, counts, onPick }: { path: string | null; counts: Record<Status, number>; onPick: () => void }) {
  return NAV.map((n) => {
    const on = isActive(path, n.href);
    const c = n.count ? counts[n.count] : 0;
    return (
      <Link key={n.href} href={n.href} onClick={onPick} className={on ? "active" : ""} aria-current={on ? "page" : undefined}>
        <Icon name={n.icon} /><span>{n.label}</span>{c > 0 && <b>{c}</b>}
      </Link>
    );
  });
}

function Crumb({ path }: { path: string | null }) {
  return <strong>{NAV.find((n) => isActive(path, n.href))?.label ?? ""}</strong>;
}

// usePathname makes a component request-time: keep it inside Suspense so the shell itself prerenders.
function ActiveNav(props: { counts: Record<Status, number>; onPick: () => void }) { return <NavLinks path={usePathname()} {...props} />; }
function ActiveCrumb() { return <Crumb path={usePathname()} />; }

export default function Shell({ children }: { children: ReactNode }) {
  const router = useRouter();
  const [open, setOpen] = useState(false);
  const [orders, setOrders] = useState<Order[]>([]);
  const [gate, setGate] = useState(false);
  const { buyer } = useSavedRules();

  useEffect(() => {
    if (!open) return;
    const esc = (e: KeyboardEvent) => e.key === "Escape" && setOpen(false);
    document.addEventListener("keydown", esc);
    return () => document.removeEventListener("keydown", esc);
  }, [open]);

  useEffect(() => {
    let live = true;
    fetch("/auth/status", { cache: "no-store" }).then((r) => r.json()).then((s) => live && setGate(Boolean(s?.gate))).catch(() => null);
    const load = () => api<Order[]>("/orders").then((o) => live && setOrders(o)).catch(() => null);
    void load();
    const t = setInterval(load, 15_000);
    return () => { live = false; clearInterval(t); };
  }, []);

  const counts = { pending_approval: 0, placed: 0, substitutes_proposed: 0, rejected: 0 } as Record<Status, number>;
  for (const o of orders) if (o.status in counts) counts[o.status] += 1;
  const pending = counts.pending_approval;
  const close = () => setOpen(false);
  const lock = async () => {
    await fetch("/auth/lock", { method: "POST" }).catch(() => null);
    router.push("/unlock");
  };

  return (
    <div className="app-shell">
      <a className="skip-link" href="#content">Skip to content</a>
      <aside id="sidebar" className={`sidebar ${open ? "open" : ""}`}>
        <Link className="brand" href="/" onClick={close}>
          <span className="brand-mark" aria-hidden="true"><span /><span /><span /></span>
          <span><strong>VIMO</strong><small>Purchasing workspace</small></span>
        </Link>
        <button className="mobile-close icon-button" onClick={close} aria-label="Close menu"><Icon name="close" /></button>
        <nav aria-label="Primary navigation">
          <p className="nav-label">Workspace</p>
          <Suspense fallback={<NavLinks path={null} counts={counts} onPick={close} />}>
            <ActiveNav counts={counts} onPick={close} />
          </Suspense>
          {/* static pages outside the Next router: plain anchors */}
          <a href="/game/index.html"><Icon name="play" /><span>Pitch: The Milk Run</span></a>
        </nav>
        <div className="sidebar-help">
          <Icon name="sparkles" size={20} />
          <strong>Need a hand?</strong>
          <p>Ask Vimo by voice or text to draft your next order.</p>
          <a href="/vimo/index.html">Ask Vimo AI <Icon name="arrow" size={14} /></a>
        </div>
        <div className="profile">
          <span className="avatar peach" aria-hidden="true">{initials(buyer || "Buyer")}</span>
          <div>
            <strong>{buyer || "No buyer rules yet"}</strong>
            <small>{buyer ? "Buyer" : "Set them with Vimo"}</small>
          </div>
          {gate && <button className="icon-button" onClick={lock} aria-label="Lock the demo" title="Lock"><Icon name="lock" /></button>}
        </div>
      </aside>

      <div className="app-main">
        <header className="app-header">
          <button className="menu-button icon-button" onClick={() => setOpen(true)} aria-label="Open menu" aria-expanded={open} aria-controls="sidebar"><Icon name="menu" /></button>
          <div className="breadcrumb">
            <span>Vimo</span><Icon name="chevron" size={14} />
            <Suspense fallback={<Crumb path={null} />}><ActiveCrumb /></Suspense>
          </div>
          <div className="header-actions">
            <Link className="notification icon-button" href="/approvals?status=pending_approval"
              aria-label={pending ? `${pending} orders waiting for approval` : "No orders waiting for approval"}>
              <Icon name="bell" />{pending > 0 && <span />}
            </Link>
            <a className="primary small" href="/vimo/index.html"><Icon name="sparkles" />Ask Vimo</a>
          </div>
        </header>
        <main id="content" className="content">{children}</main>
      </div>
      {open && <div className="backdrop mobile" onClick={close} aria-hidden="true" />}
    </div>
  );
}
