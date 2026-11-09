import type { Metadata } from "next";
import { CustomerPayment } from "../../../features/payments/customer-payment";

// Payment pages are private to the customer who received the link.
export const metadata: Metadata = { title: "Complete your payment", robots: { index: false, follow: false } };

export default async function Page({ params }: { params: Promise<{ token: string }> }) {
  const { token } = await params;
  return <CustomerPayment token={token} />;
}
