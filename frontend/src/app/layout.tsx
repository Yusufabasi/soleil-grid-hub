import type { Metadata } from "next";

import { SessionProvider } from "@/lib/session";

import "./globals.css";

export const metadata: Metadata = {
  title: "Soleil Grid Hub",
  description: "Prepaid solar energy: the customer portal and the staff console (demo build).",
};

export default function RootLayout({ children }: Readonly<{ children: React.ReactNode }>) {
  return (
    <html lang="en">
      {/* No next/font/google: the demo ships system font stacks (demo-shell-plan.md §4). */}
      <body className="antialiased">
        <SessionProvider>{children}</SessionProvider>
      </body>
    </html>
  );
}
