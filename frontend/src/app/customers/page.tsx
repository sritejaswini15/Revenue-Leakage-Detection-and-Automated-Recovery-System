import type { Metadata } from "next";
import { CustomerDirectory } from "../../features/customers/customer-directory";

export const metadata: Metadata = { title: "Customers" };

export default function Page() {
  return <CustomerDirectory />;
}
