/**
 * U5: a parameter edited in place - the number reads as plain mono text in the stage's sentence; click (or Enter / Space
 * on it) turns it into an input; Enter or leaving commits, Escape cancels, and Enter / Escape put the focus back on the
 * value so a keyboard user carries on from where they were. Selects stay native for keyboard and screen readers, with a
 * small chevron so they read as choices. Nothing here knows the stage; it reports a value.
 */
import { ChevronDown } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { cx } from "../components/primitives";
import type { ParamValue } from "./model";

const chip = "rounded-control px-1 font-mono font-tabular text-t13 text-fg underline decoration-border decoration-dotted underline-offset-4 "
  + "hover:bg-surface-2 hover:decoration-fg-muted focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand";

export interface NumberInlineProps {
  value: number;
  label: string;                 // accessible name: "RSI period"
  integer?: boolean;
  min?: number;
  onChange: (v: number) => void;
}

/** Parses what was typed; null when it is not a usable number (the edit is then dropped, never half-applied). */
export function parseInline(text: string, integer: boolean, min?: number): number | null {
  const t = text.trim();
  if (!/^-?\d*(\.\d+)?$/.test(t) || t === "" || t === "-") return null;
  const v = Number(t);
  if (!Number.isFinite(v) || (integer && !Number.isInteger(v))) return null;
  if (min !== undefined && v < min) return null;
  return v;
}

export function NumberInline({ value, label, integer = false, min, onChange }: NumberInlineProps) {
  const [editing, setEditing] = useState(false);
  const [text, setText] = useState(String(value));
  const [bad, setBad] = useState(false);
  const input = useRef<HTMLInputElement>(null);
  const button = useRef<HTMLButtonElement>(null);
  const refocus = useRef(false);                 // set by Enter / Escape; a click elsewhere keeps its own focus
  useEffect(() => { if (!editing) setText(String(value)); }, [value, editing]);
  useEffect(() => {
    if (editing) input.current?.select();
    else if (refocus.current) { refocus.current = false; button.current?.focus(); }
  }, [editing]);

  const commit = () => {
    const v = parseInline(text, integer, min);
    if (v === null) { setBad(true); return; }
    setBad(false);
    setEditing(false);
    if (v !== value) onChange(v);
  };
  if (!editing) {
    return (
      <button ref={button} type="button" className={chip} aria-label={`${label}: ${value}. Edit`} onClick={() => setEditing(true)}>
        {value}
      </button>
    );
  }
  return (
    <input
      ref={input}
      aria-label={label}
      aria-invalid={bad || undefined}
      inputMode={integer ? "numeric" : "decimal"}
      value={text}
      onChange={(e) => { setText(e.target.value); setBad(false); }}
      onBlur={() => { if (parseInline(text, integer, min) === null) { setEditing(false); setBad(false); } else commit(); }}
      onKeyDown={(e) => {
        if (e.key === "Enter") { e.preventDefault(); refocus.current = parseInline(text, integer, min) !== null; commit(); }
        if (e.key === "Escape") { e.preventDefault(); refocus.current = true; setEditing(false); setBad(false); }
      }}
      className={cx("w-16 rounded-control border bg-surface-inset px-1 font-mono font-tabular text-t13 text-fg focus:outline-none focus:ring-2",
        bad ? "border-warn focus:ring-warn" : "border-border focus:ring-brand")}
    />
  );
}

export interface SelectInlineProps {
  value: ParamValue;
  options: readonly ParamValue[];
  label: string;
  mono?: boolean;
  format?: (v: ParamValue) => string;   // the words shown for a value (an operator reads "above", not ">")
  onChange: (v: ParamValue) => void;
}

export function SelectInline({ value, options, label, mono = true, format = String, onChange }: SelectInlineProps) {
  // a native select is as wide as its longest option; the sentence reads better when it is as wide as the chosen one
  // (words in the proportional face run wider than 1ch each, hence the larger allowance; 14px is the chevron)
  const width = `calc(${Math.max(2, format(value).length) + (mono ? 0.5 : 2)}ch + 24px)`;
  return (
    <span className="relative inline-flex items-center">
      <select
        aria-label={label}
        value={String(value)}
        style={{ width }}
        onChange={(e) => {
          const picked = options.find((o) => String(o) === e.target.value);
          if (picked !== undefined) onChange(picked);
        }}
        className={cx("cursor-pointer appearance-none rounded-control border border-transparent bg-transparent py-0 pl-1 pr-4 text-t13 text-fg",
          "hover:border-border hover:bg-surface-2 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand",
          mono && "font-mono font-tabular")}
      >
        {options.map((o) => <option key={String(o)} value={String(o)}>{format(o)}</option>)}
      </select>
      <ChevronDown size={12} aria-hidden className="pointer-events-none absolute right-1 text-fg-muted" />
    </span>
  );
}

export interface TextInlineProps { value: string; label: string; placeholder?: string; onChange: (v: string) => void }

/** Free text in the sentence (an index name, a list of sector names): commits on Enter or leaving, Escape cancels. */
export function TextInline({ value, label, placeholder = "type a value", onChange }: TextInlineProps) {
  const [text, setText] = useState(value);
  useEffect(() => { setText(value); }, [value]);
  // control characters have no place in a value (ScreenQL strings do not read JSON's \uXXXX escapes)
  const clean = (t: string) => t.replace(/[\u0000-\u001f\u007f]/g, "").trim();
  const commit = () => { if (clean(text) !== value) onChange(clean(text)); };
  return (
    <input
      aria-label={label}
      value={text}
      placeholder={placeholder}
      size={Math.max(placeholder.length, text.length + 1)}
      onChange={(e) => setText(e.target.value)}
      onBlur={commit}
      onKeyDown={(e) => {
        if (e.key === "Enter") { e.preventDefault(); commit(); }
        if (e.key === "Escape") { e.preventDefault(); setText(value); }
      }}
      className="rounded-control border border-transparent bg-transparent px-1 font-mono text-t13 text-fg placeholder:text-fg-muted/60 hover:border-border focus:border-border focus:outline-none focus:ring-2 focus:ring-brand"
    />
  );
}
