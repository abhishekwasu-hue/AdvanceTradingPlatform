import { Children, isValidElement, type ReactNode } from "react";

/** PROSE-1 (owner 30 Oct): text of this many characters or more is an explanation - it goes behind an ⓘ / Details. */
export const PROSE_MIN = 60;

/** The plain text of a node (strings and numbers inside elements). */
export function nodeText(node: ReactNode): string {
  if (node === null || node === undefined || typeof node === "boolean") return "";
  if (typeof node === "string" || typeof node === "number") return String(node);
  if (Array.isArray(node)) return node.map(nodeText).join("");
  if (isValidElement<{ children?: ReactNode }>(node)) return Children.toArray(node.props.children).map(nodeText).join("");
  return "";
}

/** True for a node long enough to be an explanation rather than a label or a short format hint. */
export function isProse(node: ReactNode): boolean {
  return nodeText(node).replace(/\s+/g, " ").trim().length >= PROSE_MIN;
}
