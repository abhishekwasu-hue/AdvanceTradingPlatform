/** UI2-1: the neutral screen while it is not yet known which shell this organisation uses (data pages only, at most
 * SHELL_WAIT_MS - see shellMode and shellWait.ts). No page content, so never sample data in place of real data; a
 * visible spinner and word, announced politely. */
export default function ShellWaitingV2() {
  return (
    <div className="flex min-h-screen items-center justify-center bg-surface" aria-busy="true" data-shell="waiting">
      <div role="status" className="flex items-center gap-space-3 text-t14 text-fg-muted">
        <span aria-hidden className="h-5 w-5 animate-spin rounded-pill border-2 border-border border-t-brand" />
        Loading
      </div>
    </div>
  );
}
