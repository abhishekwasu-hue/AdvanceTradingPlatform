import type { ReactNode } from "react";
import { brokerGateFor } from "../../shell/pagesV2";
import { BrokerGate } from "../../ui/BrokerGate";
import { useFeatures } from "../../ui/useFeatures";

/** NAV-2: a page in a panel keeps the broker gate the shell puts in front of its full route (the same decision). */
export function RouteGate({ path, children }: { path: string; children: ReactNode }) {
  const gate = brokerGateFor(new URL(path, "http://app.invalid").pathname, useFeatures().on);
  return gate ? <BrokerGate storedDaily={gate.storedDaily}>{children}</BrokerGate> : <>{children}</>;
}
