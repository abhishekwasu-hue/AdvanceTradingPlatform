/**
 * N1 X1b: a scan rule's Then - the mode chip on the rule, and the drawer (mode, confirmation candles, bounded gate
 * overrides, the last check per symbol with its reason code, and the setups sent to the caller). Shown only while the
 * organisation's `scan_then` flag is on (the alert rules panel asks /system/features first; the routes answer 503 when
 * it is off). Every string comes from the `scanThen` i18n namespace. Nothing here places an order: a confirmed setup is
 * a notification or a PAPER proposal the person approves; AUTO_LIVE is never offered.
 */
import { useCallback, useEffect, useState } from "react";
import { api } from "../../api/client";
import { ApiError } from "../../api/errors";
import { Badge, Button, Input, Select } from "../../components/primitives";
import { useThenT, type ThenT } from "./i18n";
import {
  bodyOf, chipKey, codeLabel, CONFIRM_TFS, GATE_FIELDS, gateErrors, levelsVars, outcomeOf, visibleModes, withOverride,
  type ConfirmTf, type ThenBody, type ThenIntent, type ThenMode, type ThenTemplate,
} from "./model";

export function ThenChip({ template }: { template: ThenTemplate | null }) {
  const t = useThenT();
  const key = chipKey(template);
  const tone = key === "chip.off" ? "neutral" : key === "chip.paused" ? "warn" : "info";
  return <Badge tone={tone}>{t(key)}</Badge>;
}

function gateValue(body: ThenBody, key: string): string {
  const v = body.gate[key];
  return typeof v === "number" ? String(v) : "";
}

function StatusTable({ template, t }: { template: ThenTemplate; t: ThenT }) {
  const rows = Object.entries(template.status ?? {});
  if (template.status_shown === false) {
    return (
      <div className="space-y-1">
        <div className="text-xs font-medium text-fg">{t("status.title")}</div>
        <div className="text-xs text-fg-muted">{t("status.hidden")}</div>
      </div>
    );
  }
  return (
    <div className="space-y-1">
      <div className="text-xs font-medium text-fg">{t("status.title")}</div>
      {template.last_bar_at && <div className="text-xs text-fg-muted font-tabular">{t("status.lastBar", { time: new Date(template.last_bar_at).toLocaleString() })}</div>}
      {template.last_problem && <div role="alert" className="text-xs text-down">{t("status.problem", { problem: template.last_problem })}</div>}
      {rows.length === 0 ? <div className="text-xs text-fg-muted">{t("status.none")}</div> : (
        <table className="w-full text-xs">
          <thead><tr className="text-fg-muted text-left"><th className="py-1">{t("status.symbol")}</th><th>{t("status.result")}</th><th>{t("status.trend")}</th></tr></thead>
          <tbody>
            {rows.map(([symbol, st]) => (
              <tr key={symbol} className="border-t border-border align-top">
                <td className="py-1 font-medium">{symbol}</td>
                <td>
                  <div data-code={st.code}>{codeLabel(st.code, t)}</div>
                  {st.lines.map((line) => <div key={line} className="text-fg-muted font-tabular break-words">{line}</div>)}
                </td>
                <td className="text-fg-muted">{st.trend ?? ""}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </div>
  );
}

function IntentsTable({ intents, t }: { intents: ThenIntent[] | null; t: ThenT }) {
  return (
    <div className="space-y-1">
      <div className="text-xs font-medium text-fg">{t("intents.title")}</div>
      {!intents || intents.length === 0 ? <div className="text-xs text-fg-muted">{t("intents.empty")}</div> : (
        <table className="w-full text-xs">
          <thead><tr className="text-fg-muted text-left"><th className="py-1">{t("intents.when")}</th><th>{t("intents.symbol")}</th>
            <th>{t("intents.side")}</th><th>{t("intents.levels")}</th><th>{t("intents.outcome")}</th></tr></thead>
          <tbody>
            {intents.map((i) => {
              const outcome = outcomeOf(i);
              return (
                <tr key={i.id} className="border-t border-border align-top">
                  <td className="py-1 font-tabular">{i.created_at ? new Date(i.created_at).toLocaleString() : ""}</td>
                  <td className="font-medium">{i.symbol}</td>
                  <td>{i.direction === "SHORT" ? t("intents.SHORT") : t("intents.LONG")}</td>
                  <td className="font-tabular">
                    <div>{t("intents.levelsLine", levelsVars(i))}</div>
                    {i.study && <div className="text-fg-muted">{i.study}</div>}
                    {i.commit_line && <div className="text-fg-muted">{i.commit_line}</div>}
                  </td>
                  <td data-status={i.status}>{t(outcome.key, outcome.vars)}</td>
                </tr>
              );
            })}
          </tbody>
        </table>
      )}
    </div>
  );
}

export interface DrawerViewProps {
  template: ThenTemplate | null; intents: ThenIntent[] | null; body: ThenBody; onBody: (b: ThenBody) => void;
  errors: string[]; notice: string; busy: boolean; onSave: () => void; onRemove: () => void;
}

/** The drawer's content (presentational: the container below loads and saves). */
export function ThenDrawerView({ template, intents, body, onBody, errors, notice, busy, onSave, onRemove }: DrawerViewProps) {
  const t = useThenT();
  const defaults = template?.gate_defaults ?? {};
  return (
    <div className="mt-2 space-y-3 rounded border border-border px-3 py-2" data-testid="then-drawer">
      <div className="text-sm font-medium text-fg">{t("drawer.title")}</div>
      <p className="text-xs text-fg-muted">{t("drawer.intro")}</p>
      {!template?.configured && <p className="text-xs text-fg-muted">{t("drawer.notSet")}</p>}
      <div className="grid gap-2 sm:grid-cols-3">
        <Select label={t("drawer.mode")} value={body.mode} onChange={(v) => onBody({ ...body, mode: v as ThenMode })}
          options={visibleModes().map((m) => ({ value: m, label: t(`modes.${m}`) }))} />
        <Select label={t("drawer.confirmTf")} value={body.confirm_tf} onChange={(v) => onBody({ ...body, confirm_tf: v as ConfirmTf })}
          options={CONFIRM_TFS.map((tf) => ({ value: tf, label: t(`tfs.${tf}`) }))} />
        <Select label={t("drawer.enabled")} value={body.enabled ? "yes" : "no"} onChange={(v) => onBody({ ...body, enabled: v === "yes" })}
          options={[{ value: "yes", label: t("drawer.yes") }, { value: "no", label: t("drawer.no") }]} />
      </div>
      <div className="space-y-1">
        <div className="text-xs font-medium text-fg">{t("drawer.gate")}</div>
        <div className="text-xs text-fg-muted">{t("drawer.gateHint")}</div>
        <div className="grid gap-2 sm:grid-cols-2">
          {GATE_FIELDS.map((f) => f.kind === "bool" ? (
            <Select key={f.key} label={t(`gate.${f.key}`)} value={body.gate[f.key] === undefined ? "default" : body.gate[f.key] ? "yes" : "no"}
              onChange={(v) => onBody({ ...body, gate: withOverride(body.gate, f.key, v === "default" ? null : v === "yes") })}
              options={[{ value: "default", label: t("drawer.defaultValue", { value: defaults[f.key] ? t("drawer.yes") : t("drawer.no") }) },
                { value: "yes", label: t("drawer.yes") }, { value: "no", label: t("drawer.no") }]} />
          ) : (
            <Input key={f.key} label={t(`gate.${f.key}`)} type="number" min={f.min} max={f.max} step={f.step} value={gateValue(body, f.key)}
              placeholder={t("drawer.defaultValue", { value: String(defaults[f.key] ?? "") })}
              onChange={(e) => onBody({ ...body, gate: withOverride(body.gate, f.key, e.target.value) })} />
          ))}
        </div>
      </div>
      {errors.length > 0 && <ul role="alert" className="text-xs text-down space-y-0.5">{errors.map((l) => <li key={l}>{l}</li>)}</ul>}
      {notice && <div className="text-xs text-up">{notice}</div>}
      <div className="flex gap-2">
        <Button size="sm" onClick={onSave} disabled={busy}>{busy ? t("drawer.saving") : t("drawer.save")}</Button>
        {template?.configured && <Button size="sm" variant="ghost" onClick={onRemove} disabled={busy}>{t("drawer.remove")}</Button>}
      </div>
      {template?.configured && <StatusTable template={template} t={t} />}
      {template?.configured && <IntentsTable intents={intents} t={t} />}
      <p className="text-xs text-fg-muted">{t("drawer.footer")}</p>
    </div>
  );
}

function errorLines(e: unknown): string[] {
  if (e instanceof ApiError) return [e.message];
  return [String(e)];
}

/** The chip, the open / hide button and the drawer for one rule (render it only when `scan_then` is on). */
export function RuleThen({ ruleId }: { ruleId: number }) {
  const t = useThenT();
  const [template, setTemplate] = useState<ThenTemplate | null>(null);
  const [intents, setIntents] = useState<ThenIntent[] | null>(null);
  const [open, setOpen] = useState(false);
  const [body, setBody] = useState<ThenBody>(bodyOf(null));
  const [errors, setErrors] = useState<string[]>([]);
  const [notice, setNotice] = useState("");
  const [busy, setBusy] = useState(false);
  const load = useCallback(() => {
    api.ruleThen(ruleId).then((tpl) => { setTemplate(tpl); setBody(bodyOf(tpl)); }).catch((e) => setErrors(errorLines(e)));
  }, [ruleId]);
  useEffect(load, [load]);
  useEffect(() => {
    if (open && template?.configured) api.ruleThenIntents(ruleId).then(setIntents).catch((e) => setErrors(errorLines(e)));
  }, [open, ruleId, template?.configured]);
  async function save() {
    const local = gateErrors(body.gate, t);
    setErrors(local);
    setNotice("");
    if (local.length) return;
    setBusy(true);
    try {
      const tpl = await api.putRuleThen(ruleId, body);
      setTemplate(tpl);
      setBody(bodyOf(tpl));
      setNotice(t("drawer.saved"));
    } catch (e) { setErrors(errorLines(e)); } finally { setBusy(false); }
  }
  async function remove() {
    setBusy(true);
    try {
      const tpl = await api.deleteRuleThen(ruleId);
      setTemplate(tpl);
      setBody(bodyOf(tpl));
      setIntents(null);
      setNotice(t("drawer.removed"));
    } catch (e) { setErrors(errorLines(e)); } finally { setBusy(false); }
  }
  return (
    <div>
      <div className="flex items-center gap-2">
        <ThenChip template={template} />
        <Button size="sm" variant="ghost" onClick={() => setOpen((o) => !o)}>{open ? t("hide") : t("open")}</Button>
      </div>
      {open && <ThenDrawerView template={template} intents={intents} body={body} onBody={setBody} errors={errors} notice={notice}
        busy={busy} onSave={save} onRemove={remove} />}
    </div>
  );
}
