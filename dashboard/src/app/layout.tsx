import type { Metadata } from "next";
import Link from "next/link";
import "./globals.css";

export const metadata: Metadata = {
  title: "Secret Grounds Dashboard",
  description: "Cashier dashboard for Secret Grounds PC sessions"
};

export default function RootLayout({
  children
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <html lang="en">
      <body>
        <nav className="appShell">
          <Link href="/">Dashboard</Link>
          <Link href="/history">History</Link>
          <Link href="/rates">Rates</Link>
        </nav>
        {children}
      </body>
    </html>
  );
}
