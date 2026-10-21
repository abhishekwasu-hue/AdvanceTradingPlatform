import { useEffect, useState } from "react";
import { REFRESH_MS, loadPublicStatus, type StatusLoad } from "../status/publicStatus";
import { StatusView } from "../status/StatusView";

/** Z10-e (G7): the public, read-only status page at `/status`. No login and no app shell: it must render when the
 * rest of the app cannot. Polls `/api/v1/status` once a minute (the server caches the answer for 15 seconds). */
export default function StatusPage() {
  const [load, setLoad] = useState<StatusLoad>({ kind: "loading" });

  useEffect(() => {
    document.title = "Status · AiAlgoDesk";
    let alive = true;
    const run = () => loadPublicStatus().then((next) => { if (alive) setLoad(next); });
    void run();
    const id = window.setInterval(run, REFRESH_MS);
    return () => { alive = false; window.clearInterval(id); };
  }, []);

  return <StatusView load={load} />;
}
