import type { Metadata } from "next";
import { Suspense } from "react";
import { LoadingState } from "../../components/ui";
import { TransactionList } from "../../features/transactions/transaction-list";

export const metadata: Metadata = { title: "Transactions" };

export default function Page() {
  // The list reads its initial filters from the URL (?case_id=, ?search=).
  return (
    <Suspense fallback={<LoadingState label="Loading transactions…" />}>
      <TransactionList />
    </Suspense>
  );
}
