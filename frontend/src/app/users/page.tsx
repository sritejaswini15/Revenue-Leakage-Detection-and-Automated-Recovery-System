import type { Metadata } from "next";
import { UserManagement } from "../../features/users/user-management";

export const metadata: Metadata = { title: "Users" };

export default function Page() {
  return <UserManagement />;
}
