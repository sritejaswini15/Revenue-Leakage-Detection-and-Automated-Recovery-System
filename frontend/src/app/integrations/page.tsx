import type { Metadata } from "next";
import { IntegrationStatus } from "../../features/integrations/integration-status";

export const metadata: Metadata = { title: "Integrations" };

export default function Page() {
  return <IntegrationStatus />;
}
