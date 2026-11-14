import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = { title: "Vimo — Purchasing workspace", description: "Trusted purchasing decisions with Vimo." };

export default function RootLayout({ children }: LayoutProps<"/">) {
  return (
    <html lang="en">
      <body>{children}</body>
    </html>
  );
}
