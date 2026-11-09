import type { Metadata } from "next";
import { AuditTrail } from "../../features/audit/audit-trail";

export const metadata: Metadata = { title: "Audit trail" };

export default function Page() {
  return <AuditTrail />;
}
