import type { Metadata } from "next";
import { CaseQueue } from "../../features/cases/case-queue";

export const metadata: Metadata = { title: "Cases" };

export default function Page() {
  return <CaseQueue />;
}
