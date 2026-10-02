import type { Hono, MiddlewareHandler } from 'hono';
import { bodyLimit } from 'hono/body-limit';
import { z } from 'zod';
import { channels, type IpcResult } from '@desk/bff/contract';
import { dispatch, toIpcError, type HandlerContext } from '@desk/bff/server';
import type { Sessions } from './auth';
import { callerOf, type Caller } from './caller';
import type { DeviceStore } from './devices';
import { decodeBytes, encodeBytes } from './codec';
import { PUSH_OPS, SESSION_HEADER } from './frames';
import { phoneRefusal } from './phone-policy';
import { slimResultForPhone } from './phone-slim';
import { originCheck, type RemoteOrigin } from './security';
import { webChannels, type WebChannel, type WebChannelOutput } from './web-channels';

/** 40 MB: a 25 MB library upload is about 34 MB as base64 JSON. */
export const MAX_RPC_BODY = 40 * 1024 * 1024;

export type WebHandlers = { [C in WebChannel]: (input: z.output<(typeof webChannels)[C]>) => Promise<WebChannelOutput<C>> };

export type RpcDeps = {
  port: () => number;
  /** The remote address phones use, when one is set. */
  remote?: () => RemoteOrigin | null;
  sessions: Sessions;
  /** Paired phones: their secrets work too, for the operations `PHONE_OPS` lists. */
  devices?: DeviceStore;
  /** The HandlerContext /rpc calls run with. It has no socket: broker.watch and unwatch run on /push. */
  ctx(): HandlerContext;
  web: WebHandlers;
  maxBodyBytes?: number;
  log?(message: string, err?: unknown): void;
};

const pushOps = new Set<string>(PUSH_OPS);

/** A JSON answer that is never cached. */
export function rpcJson(status: number, body: unknown): Response {
  return new Response(JSON.stringify(body), { status, headers: { 'content-type': 'application/json; charset=utf-8', 'cache-control': 'no-store' } });
}

const refusal = (status: number, code: string, message: string) => rpcJson(status, { ok: false, error: { code, message } });

/** Validates and runs one web-only operation, as `dispatch` does for `channels`. Never throws. */
export async function dispatchWeb(op: WebChannel, input: unknown, web: WebHandlers, log?: (err: unknown) => void): Promise<IpcResult<unknown>> {
  const parsed = webChannels[op].safeParse(input ?? {});
  if (!parsed.success) return { ok: false, error: { code: 'invalid_request', message: z.prettifyError(parsed.error).slice(0, 500) } };
  try {
    return { ok: true, value: await (web[op] as (i: unknown) => Promise<unknown>)(parsed.data) };
  } catch (err) {
    return { ok: false, error: toIpcError(err, log) };
  }
}

function sessionCheck(sessions: Sessions, devices?: DeviceStore): MiddlewareHandler<{ Variables: { caller: Caller } }> {
  return async (c, next) => {
    const caller = callerOf(c.req.header(SESSION_HEADER), sessions, devices);
    if (!caller) return refusal(401, 'unauthorized', 'Signed out. Open Desk again from your terminal with desk web.');
    c.set('caller', caller);
    await next();
  };
}

const jsonOnly: MiddlewareHandler = async (c, next) => {
  const type = (c.req.header('content-type') ?? '').split(';')[0]?.trim().toLowerCase();
  if (type !== 'application/json') return refusal(415, 'unsupported_media_type', 'Send JSON (content-type: application/json).');
  await next();
};

/** POST /rpc/:op: Origin, session and JSON checks, a 40 MB limit, then the operation's schema and handler. */
export function mountRpc(app: Hono, d: RpcDeps): void {
  const limit = bodyLimit({ maxSize: d.maxBodyBytes ?? MAX_RPC_BODY, onError: () => refusal(413, 'too_large', 'That request is too large (the limit is 40 MB).') });
  app.post('/rpc/:op', originCheck(d.port, d.remote), sessionCheck(d.sessions, d.devices), jsonOnly, limit, async (c) => {
    const op = c.req.param('op') ?? '';
    let input: unknown;
    try {
      input = decodeBytes(await c.req.json());
    } catch {
      return refusal(400, 'invalid_request', 'The request body is not valid JSON, or holds a bad $bytes value.');
    }
    if (pushOps.has(op)) return refusal(400, 'push_only', `${op} runs on the /push socket.`);
    if (c.get('caller').kind === 'phone') {
      const refused = phoneRefusal(op, input);
      if (refused) return refusal(403, 'not_on_phone', refused);
    }
    const log = (err: unknown) => d.log?.(`rpc ${op.slice(0, 64)} failed`, err);
    let result: IpcResult<unknown>;
    if (Object.hasOwn(channels, op)) result = await dispatch(op, input, d.ctx(), log);
    else if (Object.hasOwn(webChannels, op)) result = await dispatchWeb(op as WebChannel, input, d.web, log);
    else return refusal(404, 'unknown_channel', `Unknown operation: ${op.slice(0, 64)}`);
    // A paired phone gets event pages with long tool texts cut (phone-slim.ts), as on /push.
    if (result.ok && c.get('caller').kind === 'phone') result = { ok: true, value: slimResultForPhone(op, result.value) };
    return rpcJson(!result.ok && result.error.code === 'invalid_request' ? 400 : 200, encodeBytes(result));
  });
}
