import { cx } from "../../components/primitives/cx";
import { Details } from "../../ui/Details";
import { annotationRows, type Annotations } from "./annotations";

/**
 * M75 W-3 (owner order M75 CW-2): the workspace reading's short card + table - numbers first, the source in one line,
 * each area in the closed Details. Every word and number is the server's (`annotations`); green up / red down by meaning.
 */
const TONE: Record<"up" | "down" | "flat", string> = { up: "text-up", down: "text-down", flat: "text-fg" };

export function AnnotationsV2({ annotations: a }: { annotations: Annotations }) {
  const rows = annotationRows(a);
  return (
    <div data-workspace-annotations={a.source} className="space-y-space-2 text-t13 text-fg">
      <table className="w-full table-fixed text-t13 tabular-nums">
        <tbody>
          {rows.map((r) => (
            <tr key={r.key} data-row={r.key} className="border-b border-subtle last:border-b-0 align-top">
              <th scope="row" className="w-2/5 py-space-1 pr-space-2 text-left font-normal text-fg-muted">{r.label}</th>
              <td className={cx("break-words py-space-1 font-medium", TONE[r.tone ?? "flat"])}>{r.value}</td>
            </tr>
          ))}
        </tbody>
      </table>
      <p className="text-t12 text-fg-muted" data-workspace-source>{a.source_words}</p>
      {(a.areas.length > 0 || a.summary.length > 0) && (
        <Details id="copilot.workspace.areas" label="Areas and how it reads">
          {a.summary.length > 0 && (
            <ul className="list-disc space-y-space-1 pl-space-4">
              {a.summary.map((line, i) => <li key={i}>{line}</li>)}
            </ul>
          )}
          {a.areas.length > 0 && (
            <ul className="mt-space-2 space-y-space-1" aria-label="Areas on this chart">
              {a.areas.map((x, i) => (
                <li key={`${x.role}-${x.low}-${x.high}-${i}`} className={cx("border-l-2 pl-space-2", x.role === "demand" ? "border-up" : "border-down")}>{x.words}</li>
              ))}
            </ul>
          )}
        </Details>
      )}
    </div>
  );
}

export default AnnotationsV2;
