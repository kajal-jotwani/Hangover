// scratch/in_memory_cache.ts — performance experiment (CI smoke test for CodeMind)
//
// Replacing the Redis cache layer with a per-process in-memory Map.
// cacheGet and cacheSet now read/write a local Map<string,string> instead of Redis.

const cache = new Map<string, string>();

export function cacheGet(key: string): string | undefined {
  return cache.get(key);
}

export function cacheSet(key: string, value: string): void {
  cache.set(key, value);
}