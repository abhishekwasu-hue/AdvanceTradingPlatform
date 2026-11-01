import type { MouseEvent } from "react";
import { Link, useInRouterContext, type LinkProps } from "react-router";
import { useInPlace } from "./InPlaceProvider";

type InPlaceLinkProps = Omit<LinkProps, "to"> & {
  to: string;
  /** NAV-4: the link opened a new browser tab before - it still does where no panel shows the address (the classic
   * shell), and opens in place where one does. */
  newTabOtherwise?: boolean;
};

/** A Link's own props that an `<a>` does not take. */
const ROUTER_ONLY = ["to", "newTabOtherwise", "state", "replace", "reloadDocument", "relative", "preventScrollReset", "viewTransition", "discover", "prefetch"];

/**
 * M75 NAV-1: a drop-in for `<Link to>`. It keeps the real address in `href` (so a long-press, a middle-click or "open
 * in new tab" still gives the full page), and a plain click opens the panel registered for that address in place.
 * With no panel registered (or `ui_v2` off) it is an ordinary link, exactly as today.
 */
export function InPlaceLink(props: InPlaceLinkProps) {
  // NAV-4: a part drawn outside any router (the order ticket and the basket in their own tests) keeps the plain link it
  // had before; inside the app there is always a router.
  if (!useInRouterContext()) {
    const anchor: Record<string, unknown> = { ...props };
    for (const k of ROUTER_ONLY) delete anchor[k];
    return <a {...anchor} href={props.to} {...(props.newTabOtherwise ? { target: "_blank" } : {})} />;
  }
  return <RoutedInPlaceLink {...props} />;
}

function RoutedInPlaceLink({ to, onClick, newTabOtherwise, ...rest }: InPlaceLinkProps) {
  const inPlace = useInPlace();
  if (newTabOtherwise && !rest.target && !inPlace.handles(to)) rest.target = "_blank";
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
