/**
 * REV-1 M3: one shared read. Two parts of a page that ask for the same answer at the same time (the Watchlist page and
 * the list panel inside it both read the ready-made list groups) share one request: a caller that asks while a read is
 * in flight gets that read's answer. Once it has answered, the next ask reads again, so nothing goes stale.
 */
const inFlight = new Map<string, Promise<unknown>>();

export function sharedRead<T>(key: string, load: () => Promise<T>): Promise<T> {
  const running = inFlight.get(key) as Promise<T> | undefined;
  if (running) return running;
  const read = load().finally(() => { inFlight.delete(key); });
  inFlight.set(key, read);
  return read;
}

/** Wraps a read so that callers at the same time share one request (see sharedRead). */
export function shared<T>(key: string, load: () => Promise<T>): () => Promise<T> {
  return () => sharedRead(key, load);
}
