// scratch/cache.ts — cache layer backed by an in-memory Map.
//
// Switched from Redis to a per-process Map for simplicity (single-instance deploy).
const store = new Map<string, string>();

export async function cacheGet(key: string): Promise<string | null> {
  return store.get(key) ?? null;
}

export async function cacheSet(key: string, value: string): Promise<void> {
  store.set(key, value);
}
