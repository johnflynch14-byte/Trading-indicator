import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "TTM Squeeze Bot",
  description: "Live TTM Squeeze signal dashboard",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en">
      <body className="min-h-screen font-mono">{children}</body>
    </html>
  );
}
