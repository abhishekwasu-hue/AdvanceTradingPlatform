import { useEffect, useState } from "react";
import { Link } from "react-router";
import { LOOK_INSIDE_PATH, guestSnapshot } from "./lookInside";

/** M95 PR-5: the sign-in screen's one line to the guest view - nothing while it is closed (no stored day, flag off). */
export default function LookInsideLink() {
  const [on, setOn] = useState(false);
  useEffect(() => {
    let live = true;
    guestSnapshot().then((s) => { if (live) setOn(s !== null); });
    return () => { live = false; };
  }, []);
  if (!on) return null;
  return (
    <Link to={LOOK_INSIDE_PATH} data-look-inside-link
      className="inline-flex min-h-11 items-center rounded-control text-t12 font-medium text-brand hover:underline md:min-h-0 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand">
      Look inside without signing in
    </Link>
  );
}
