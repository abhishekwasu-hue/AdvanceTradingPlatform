import type { MouseEvent } from "react";
import { Link, type LinkProps } from "react-router";
import { useInPlace } from "./InPlaceProvider";

/**
 * M75 NAV-1: a drop-in for `<Link to>`. It keeps the real address in `href` (so a long-press, a middle-click or "open
 * in new tab" still gives the full page), and a plain click opens the panel registered for that address in place.
 * With no panel registered (or `ui_v2` off) it is an ordinary link, exactly as today.
 */
export function InPlaceLink({ to, onClick, ...rest }: Omit<LinkProps, "to"> & { to: string }) {
  const inPlace = useInPlace();
  const click = (e: MouseEvent<HTMLAnchorElement>) => {
    onClick?.(e);
    if (e.defaultPrevented || e.button !== 0 || e.metaKey || e.ctrlKey || e.shiftKey || e.altKey) return;
    if ((rest.target && rest.target !== "_self") || rest.reloadDocument) return;
    if (!inPlace.handles(to)) return;   // no panel for it: the link navigates as before
    e.preventDefault();
    inPlace.open(to, rest.state === undefined ? undefined : { state: rest.state });   // NAV-2: the handed state rides along
  };
  return <Link to={to} onClick={click} {...rest} />;
}
