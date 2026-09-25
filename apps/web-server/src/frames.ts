import { z } from 'zod';
import type { IpcResult, PushChannel } from '@desk/bff/contract';

/** The header every /rpc call carries the session secret in. */
export const SESSION_HEADER = 'x-desk-session';
/** Where the browser keeps its session secret (localStorage, so per origin: this port only). */
export const SESSION_STORAGE_KEY = 'desk.session';
/** /push close codes: no valid session (the UI signs out), and a malformed frame after sign-in. */
export const CLOSE_UNAUTHORIZED = 4401;
export const CLOSE_BAD_FRAME = 4400;
/** How long /push waits for the session frame. */
export const AUTH_TIMEOUT_MS = 5000;

/** The desktop app's push channels plus the web-only desk:notify. */
export type WebPushChannel = PushChannel | 'desk:notify';

/** A browser notification for a new attention item; desk:notify's payload is WebNotice[]. `tag` is the item id. */
export type WebNotice = { tag: string; title: string; body: string; route: string };

export const NotifyPermission = z.enum(['granted', 'denied', 'default']);
export type NotifyPermission = z.infer<typeof NotifyPermission>;

/** The first client frame on /push. */
export const SessionFrame = z.object({ session: z.string().min(1).max(200) });

/** Client frames after sign-in: broker.watch and broker.unwatch requests, and the page's Notification.permission. */
export const PushClientFrame = z.union([
  z.object({ op: z.string().min(1).max(64), id: z.number().int().min(0), input: z.unknown() }),
  z.object({ notifyPermission: NotifyPermission }),
]);
export type PushClientFrame = z.infer<typeof PushClientFrame>;

/** Server frames: a push, or the answer to a request (sent after the backfill frames the request caused). */
export type PushServerFrame = { channel: WebPushChannel; payload: unknown } | { ack: number; result: IpcResult<unknown> };

/** The operations that run on /push (their sender is the socket); /rpc refuses them. */
export const PUSH_OPS = ['broker.watch', 'broker.unwatch'] as const;
