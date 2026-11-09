import type { Metadata } from "next";
import { SimulationCenter } from "../../features/simulator/simulation-center";

export const metadata: Metadata = { title: "Simulator" };

export default function Page() {
  return <SimulationCenter />;
}
