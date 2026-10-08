import * as RT from "@radix-ui/react-tabs";
import type { ReactNode } from "react";
import { cx } from "./cx";

export interface TabItem { value: string; label: ReactNode; content: ReactNode }

/** Tabs with arrow-key navigation. Controlled (`value` / `onChange`) so a page can mirror the tab in its URL. */
export function Tabs({ items, value, onChange, className }: { items: TabItem[]; value: string; onChange: (v: string) => void; className?: string }) {
  return (
    <RT.Root value={value} onValueChange={onChange} className={className}>
      <RT.List className="flex gap-1 overflow-x-auto border-b border-border">
        {items.map((t) => (
          <RT.Trigger key={t.value} value={t.value}
            className="-mb-px whitespace-nowrap border-b-2 border-transparent px-3 py-2 text-sm font-medium text-fg-muted hover:text-fg data-[state=active]:border-brand data-[state=active]:text-fg focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand">
            {t.label}
          </RT.Trigger>
        ))}
      </RT.List>
      {items.map((t) => <RT.Content key={t.value} value={t.value} className="pt-4 focus:outline-none">{t.content}</RT.Content>)}
    </RT.Root>
  );
}
