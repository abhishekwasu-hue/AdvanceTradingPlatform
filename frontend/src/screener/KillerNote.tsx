/**
 * The note under a scan whose condition left no symbol: which stage, and ONE plain sentence that never claims more than
 * the run checked - with symbols that could not be read, "removes all 45 symbols it could check", not "every symbol".
 */
import { Button } from "../components/primitives";

export interface KillerNoteProps {
  /** the symbols the stage removed (all that reached it) */
  removed: number | null;
  /** of the run's symbols, how many could not be read or checked */
  unread: number;
  onShow: () => void;
  onDisable: () => void;
}

export function killerLine(removed: number | null, unread: number): string {
  const what = unread > 0 && removed != null && removed > 0
    ? (removed === 1 ? "the one symbol it could check" : `all ${removed} symbols it could check`)
    : "every symbol";
  return `This condition removes ${what}. Loosen it or disable it to see candidates.`;
}

export function KillerNote({ removed, unread, onShow, onDisable }: KillerNoteProps) {
  return (
    <div role="status" className="rounded-panel border border-warn/50 bg-surface-1 p-3 text-t13 text-fg" data-testid="scan-killer">
      <p>{killerLine(removed, unread)}</p>
      <div className="mt-2 flex gap-2">
        <Button size="sm" onClick={onShow}>Show the stage</Button>
        <Button size="sm" onClick={onDisable}>Disable it</Button>
      </div>
    </div>
  );
}

export default KillerNote;
