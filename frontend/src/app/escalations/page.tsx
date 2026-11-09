import type { Metadata } from "next";
import { EscalationQueue } from "../../features/escalations/escalation-queue";

export const metadata: Metadata = { title: "Escalations" };

export default function Page() {
  return <EscalationQueue />;
}
