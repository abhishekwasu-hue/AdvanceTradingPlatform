import { useEffect, useState } from "react";
import { REFRESH_MS, loadPublicStatus, type StatusLoad } from "../status/publicStatus";
import { StatusView } from "../status/StatusView";
import { loadStatusSessions, type SessionsLoad } from "../status/sessions";

/** Z10-e (G7): the public, read-only status page at `/status`. No login and no app shell: it must render when the
 * rest of the app cannot. Polls `/api/v1/status` once a minute (the server caches the answer for 15 seconds). */
export default function StatusPage() {
  const [load, setLoad] = useState<StatusLoad>({ kind: "loading" });
  // AUD-T8: uptime per market session and the service targets (flag status_page; absent while off).
  const [sessions, setSessions] = useState<SessionsLoad>({ kind: "loading" });

  useEffect(() => {
    document.title = "Status · AiAlgoDesk";
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
