import type { Metadata } from "next";
import type { ReactNode } from "react";
import "./globals.css";

export const metadata: Metadata = {
  title: { default: "RecoverX | Revenue Recovery Control Plane", template: "%s · RecoverX" },
  description: "Policy-controlled revenue recovery operations for Razorpay merchants.",
};

export default function RootLayout({ children }: Readonly<{ children: ReactNode }>) {
  return (
    <html lang="en">
      <body>{children}</body>
    </html>
  );
}
