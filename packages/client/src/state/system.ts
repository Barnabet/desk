import type { HealthResponse, StoredEvent } from '@desk/protocol';

export type Notice = { eventId: number; ts: string; projectId: string; level: 'info' | 'warning' | 'error'; code: string; message: string };
export type SystemState = { proxy: 'up' | 'down' | 'unknown'; notices: Notice[]; lastSeq: number };

const MAX_NOTICES = 200;

export const systemFromHealth = (h: HealthResponse): SystemState => ({ proxy: h.proxy ?? 'unknown', notices: [], lastSeq: 0 });

export function reduceSystem(prev: SystemState, e: StoredEvent): SystemState {
  if (e.id <= prev.lastSeq) return prev;
  if (e.type !== 'system.notice') return { ...prev, lastSeq: e.id };
  const notice: Notice = { eventId: e.id, ts: e.ts, projectId: e.project_id, level: e.payload.level, code: e.payload.code, message: e.payload.message };
  const proxy = e.payload.code === 'proxy_down' ? 'down' : e.payload.code === 'proxy_up' ? 'up' : prev.proxy;
  return { proxy, notices: [...prev.notices, notice].slice(-MAX_NOTICES), lastSeq: e.id };
}
