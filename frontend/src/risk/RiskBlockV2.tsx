import { Link } from "react-router";
import type { RiskBlock } from "../settings/riskLimits";

/**
 * M64 RM-3: one block message - never silent, never a raw reason. One plain sentence with its numbers (from the server),
 * a "Change limit" link to the exact check on Settings › Risk limits (or "Ask your admin" when an organisation ceiling
 * applied), and for a size limit the smaller size the trader may place instead ("Place 5 lots").
 */
export default function RiskBlockV2({ block, onSmaller, busy = false, testId = "risk-block" }: {
  block: RiskBlock;
  /** Checks the order again at the smaller size the limit allows (the confirm step then places it); shown only with it. */
  onSmaller?: (lots: number) => void;
  busy?: boolean;
  testId?: string;
}) {
  const smaller = block.smaller_lots !== null && block.smaller_lots > 0 ? block.smaller_lots : null;
  return (
    <div role="alert" className="basis-full space-y-space-2 rounded-control bg-warn-tint px-space-3 py-space-2" data-testid={testId}
      data-check={block.check}>
      <p className="text-t13 text-fg">{block.text}</p>
      <div className="flex flex-wrap items-center gap-space-3">
        {smaller !== null && onSmaller && (
          <button type="button" disabled={busy} onClick={() => onSmaller(smaller)} data-risk-block="smaller"
            className="min-h-11 rounded-control bg-brand px-space-3 text-t14 font-semibold text-on-brand disabled:opacity-50 md:min-h-9">
            Use {smaller} lot{smaller === 1 ? "" : "s"}
          </button>
        )}
        {block.admin || !block.link ? (
          <span className="text-t13 font-medium text-fg-muted" data-risk-block="admin">Ask your admin</span>
        ) : (
          <Link to={block.link} className="inline-flex min-h-11 items-center text-t14 font-medium text-brand underline md:min-h-9" data-risk-block="link">
            Change limit
          </Link>
        )}
      </div>
    </div>
  );
}
