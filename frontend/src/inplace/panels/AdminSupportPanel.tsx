import { SupportPanel } from "../../admin/AdminSectionsV2";
import type { PanelProps } from "../registry";

/** NAV-8: one user's support status in place (a side panel over the admin console's user list): the same panel as the
 * console's Support section; picking another user changes the panel in place. The server still decides who may see it. */
export default function AdminSupportPanel({ params, replace }: PanelProps) {
  const id = Number.parseInt(params.user ?? "", 10);
  return <SupportPanel userId={Number.isFinite(id) && id > 0 ? id : null} onUser={(next) => replace(next == null ? {} : { user: String(next) })} />;
}
