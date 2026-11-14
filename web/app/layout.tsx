import type { Metadata } from "next";
import Link from "next/link";
import LockState from "./LockState";
import "./globals.css";

export const metadata: Metadata = { title: "Vimo by Aimo" };

export default function RootLayout({ children }: LayoutProps<"/">) {
  return (
    <html lang="en">
      <body>
        <header className="topbar">
          <div>
            <Link className="brand" href="/">VIMO <span>BY AIMO</span></Link>
            <nav aria-label="Main">
              {/* static pages outside the Next router: plain anchors */}
              <a href="/vimo/index.html">Order (Vimo)</a>
              <Link href="/approvals">Approvals</Link>
              <a href="/game/index.html">Pitch</a>
            </nav>
            <LockState />
          </div>
        </header>
        <main>{children}</main>
      </body>
    </html>
  );
}
