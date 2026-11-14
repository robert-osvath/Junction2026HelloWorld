import type { Metadata } from "next";
import Link from "next/link";
import "./globals.css";

export const metadata: Metadata = { title: "Aimo Order Approvals" };

export default function RootLayout({ children }: LayoutProps<"/">) {
  return (
    <html lang="en">
      <body>
        <header><Link href="/">Aimo Order Approvals</Link></header>
        <main>{children}</main>
      </body>
    </html>
  );
}
