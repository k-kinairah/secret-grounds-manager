import type { Metadata } from "next";
import { AuthShell } from "@/components/AuthShell";
import "./globals.css";

export const metadata: Metadata = {
  title: "Secret Grounds",
  description: "Cashier dashboard for Secret Grounds PC sessions",
  icons: {
    icon: "/secret-grounds-logo.png",
    shortcut: "/secret-grounds-logo.png",
    apple: "/secret-grounds-logo.png"
  }
};

export default function RootLayout({
  children
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <html lang="en">
      <body>
        <AuthShell>{children}</AuthShell>
      </body>
    </html>
  );
}
