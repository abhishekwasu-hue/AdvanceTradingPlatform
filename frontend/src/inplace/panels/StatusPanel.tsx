import { useEffect, useState } from "react";
import { REFRESH_MS, loadPublicStatus, type StatusLoad } from "../../status/publicStatus";
import { StatusView } from "../../status/StatusView";
import { loadStatusSessions, type SessionsLoad } from "../../status/sessions";

/** NAV-6b: the service status in place (a modal over the tab): the same view and refresh as the public `/status` page,
 * without touching the browser tab's title (the tab underneath keeps its own). */
export default function StatusPanel() {
  const [load, setLoad] = useState<StatusLoad>({ kind: "loading" });
  const [sessions, setSessions] = useState<SessionsLoad>({ kind: "loading" });
  useEffect(() => {
    let alive = true;
    const run = () => {
      void loadPublicStatus().then((next) => { if (alive) setLoad(next); });
      void loadStatusSessions().then((next) => { if (alive) setSessions(next); });
    };
    run();
    const id = window.setInterval(run, REFRESH_MS);
    return () => { alive = false; window.clearInterval(id); };
  }, []);
  return <StatusView load={load} sessions={sessions} />;
}
