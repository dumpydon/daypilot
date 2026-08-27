import type { Metadata } from "next";
import { Geist, Geist_Mono } from "next/font/google";
import "./globals.css";

const geistSans = Geist({
  variable: "--font-geist-sans",
  subsets: ["latin"],
});

const geistMono = Geist_Mono({
  variable: "--font-geist-mono",
  subsets: ["latin"],
});

export const metadata: Metadata = {
  metadataBase: new URL(process.env.NEXT_PUBLIC_SITE_URL ?? "http://localhost:3000"),
  title: "DayPilot — MCP-powered personal operations",
  description: "An auditable operations agent with MCP tools and human-approved execution.",
  openGraph: {
    title: "DayPilot — MCP-powered personal operations",
    description: "An auditable operations agent with MCP tools and human-approved execution.",
    type: "website",
    images: [{ url: "/og.png", width: 1200, height: 630, alt: "DayPilot workflow" }],
  },
  twitter: {
    card: "summary_large_image",
    title: "DayPilot — MCP-powered personal operations",
    description: "An auditable operations agent with MCP tools and human-approved execution.",
    images: ["/og.png"],
  },
};

export default function RootLayout({ children }: LayoutProps<"/">) {
  return (
    <html lang="en" className={`${geistSans.variable} ${geistMono.variable}`}>
      <body>{children}</body>
    </html>
  );
}
