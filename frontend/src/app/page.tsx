import type { Metadata } from "next";
import { CommandCenter } from "../features/dashboard/command-center";

export const metadata: Metadata = { title: "Command center" };

export default function Page() {
  return <CommandCenter />;
}
