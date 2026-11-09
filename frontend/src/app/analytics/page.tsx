import type { Metadata } from "next";
import { AnalyticsOverview } from "../../features/analytics/analytics-overview";

export const metadata: Metadata = { title: "Analytics" };

export default function Page() {
  return <AnalyticsOverview />;
}
