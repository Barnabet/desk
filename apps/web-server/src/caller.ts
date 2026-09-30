import type { Sessions } from './auth';
import type { DeviceStore } from './devices';

/** Who holds a session secret: a browser on this Mac (a login link's session), or a paired phone. */
export type Caller = { kind: 'browser' } | { kind: 'phone'; deviceId: string };

/** The caller a session secret belongs to, or null. */
export function callerOf(secret: string | null | undefined, sessions: Sessions, devices?: DeviceStore): Caller | null {
  if (sessions.valid(secret)) return { kind: 'browser' };
  const device = devices?.who(secret);
  return device ? { kind: 'phone', deviceId: device.id } : null;
}
