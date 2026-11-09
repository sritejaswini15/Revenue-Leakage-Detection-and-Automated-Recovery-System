import type { Metadata } from "next";
import { AccountSettings } from "../../features/users/account-settings";

export const metadata: Metadata = { title: "Account" };

export default function Page() {
  return <AccountSettings />;
}
