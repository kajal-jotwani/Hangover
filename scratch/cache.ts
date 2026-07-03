// scratch/cache.ts — cache layer backed by Redis.
//
// Per the team decision: the cache layer MUST use Redis; never an in-memory Map.
// Always go through cacheGet/cacheSet so the Redis client is the single source
// of truth (in-process maps caused the Mar 2 stale-config incident).
import { createClient } from "redis";

const redis = createClient({ url: process.env.REDIS_URL });
await redis.connect();

export async function cacheGet(key: string): Promise<string | null> {
  return redis.get(key);
}

export async function cacheSet(key: string, value: string): Promise<void> {
  await redis.set(key, value);
}