import { useEffect, useState } from "react";
import { journalApi } from "./api";
import { LastGoodV2 } from "./ProofV2";
import type { JournalToday } from "./types";

/**
 * P2-8 J4 (Today page, flag `discipline_engine`): "Your last good decision" from his own journal - a fact with its date,
 * and after a loss how often he followed his plan in his last trades. Shows nothing while there is none, or the journal
 * is off for him.
 */
export default function JournalLastGoodV2({ api = journalApi }: { api?: Pick<typeof journalApi, "today"> }) {
  const [data, setData] = useState<JournalToday | null>(null);
  useEffect(() => {
    let alive = true;
    api.today().then((d) => { if (alive) setData(d); }).catch(() => { /* intentional: the card stays hidden */ });
    return () => { alive = false; };
  }, [api]);
  if (!data) return null;
  return <LastGoodV2 fact={data.last_good} afterLoss={data.after_loss} />;
}
