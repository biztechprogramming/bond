/**
 * Deployment Queue — per-environment FIFO queue for deploy requests
 * that arrive while the environment lock is held.
 *
 * In-memory only — queues don't survive restarts, which is acceptable
 * since deployments are retried by agents.
 */

export interface QueueEntry {
  script_id: string;
  version: string;
  agent_sub: string;
  queued_at: string;
  priority: number;
}

const queues = new Map<string, QueueEntry[]>();

export function enqueue(env: string, entry: QueueEntry): number {
  if (!queues.has(env)) queues.set(env, []);
  const q = queues.get(env)!;
  q.push(entry);
  // Sort by priority descending (higher = more urgent), then FIFO by queued_at
  q.sort((a, b) => b.priority - a.priority || a.queued_at.localeCompare(b.queued_at));
  return q.indexOf(entry) + 1; // 1-based position
}

export function dequeue(env: string): QueueEntry | null {
  const q = queues.get(env);
  if (!q || q.length === 0) return null;
  return q.shift()!;
}

export function peek(env: string): QueueEntry | null {
  const q = queues.get(env);
  if (!q || q.length === 0) return null;
  return q[0]!;
}

export function getQueue(env: string): QueueEntry[] {
  return queues.get(env) ?? [];
}

export function removeFromQueue(env: string, script_id: string, version: string): boolean {
  const q = queues.get(env);
  if (!q) return false;
  const idx = q.findIndex(e => e.script_id === script_id && e.version === version);
  if (idx === -1) return false;
  q.splice(idx, 1);
  return true;
}
