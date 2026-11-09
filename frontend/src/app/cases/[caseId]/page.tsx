import type { Metadata } from "next";
import { CaseInvestigation } from "../../../features/cases/case-investigation";

type Props = { params: Promise<{ caseId: string }> };

export async function generateMetadata({ params }: Props): Promise<Metadata> {
  const { caseId } = await params;
  return { title: `Case ${decodeURIComponent(caseId)}` };
}

export default async function Page({ params }: Props) {
  const { caseId } = await params;
  return <CaseInvestigation caseId={decodeURIComponent(caseId)} />;
}
