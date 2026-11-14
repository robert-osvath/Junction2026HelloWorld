import type { Metadata } from "next";
import { DM_Sans, Manrope } from "next/font/google";
import "./globals.css";

// Self-hosted at build time: no runtime request to Google.
const body = DM_Sans({ subsets: ["latin", "latin-ext"], variable: "--font-body" });
const heading = Manrope({ subsets: ["latin", "latin-ext"], weight: ["600", "700", "800"], variable: "--font-heading" });

export const metadata: Metadata = { title: "Vimo by Aimo", description: "Purchasing workspace: buyer agents order, a jury decides, people approve." };

export default function RootLayout({ children }: LayoutProps<"/">) {
  return (
    <html lang="en" className={`${body.variable} ${heading.variable}`}>
      <body>{children}</body>
    </html>
  );
}
