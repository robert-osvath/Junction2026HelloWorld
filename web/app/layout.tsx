import type { Metadata } from "next";
import Link from "next/link";
import "./globals.css";

export const metadata: Metadata = { title: "Approval inbox" };

export default function RootLayout({ children }: LayoutProps<"/">) {
  return (
    <html lang="en">
      <body>
        <header><Link href="/">Trusted Trade Agents: approval inbox</Link></header>
        <main>{children}</main>
      </body>
    </html>
  );
}
