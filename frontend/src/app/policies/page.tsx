import type { Metadata } from "next";
import { PolicySettings } from "../../features/policies/policy-settings";

export const metadata: Metadata = { title: "Policies" };

export default function Page() {
  return <PolicySettings />;
}
