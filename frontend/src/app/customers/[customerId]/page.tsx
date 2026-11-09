import type { Metadata } from "next";
import { CustomerProfile } from "../../../features/customers/customer-profile";

type Props = { params: Promise<{ customerId: string }> };

export const metadata: Metadata = { title: "Customer" };

export default async function Page({ params }: Props) {
  const { customerId } = await params;
  return <CustomerProfile customerId={decodeURIComponent(customerId)} />;
}
