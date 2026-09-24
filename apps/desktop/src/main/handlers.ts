import { z } from 'zod';
import { ApiError, DaemonNotRunning, DaemonUnavailable, ProtocolMismatch, type DeskClient } from '@desk/client';
import { channels, type AppSettings, type AppSettingsPatch, type Channel, type IpcError, type IpcResult } from '../shared/ipc';
import type { GlobalState } from '../shared/state';
import type { DaemonStatus } from './daemon';
import { UserFacingError } from './errors';

export type AppInfo = { version: string; platform: NodeJS.Platform; packaged: boolean; dataDir: string };

/** What a handler can reach. Built per call in main; faked in tests. */
export type HandlerContext = {
  senderId: number;
  client(): DeskClient;
  broker: {
    snapshot(): GlobalState;
    watch(senderId: number, projectId: string, afterSeq: number): Promise<void>;
    unwatch(senderId: number, projectId: string): void;
  };
  daemon: { status(): Promise<DaemonStatus>; start(): Promise<DaemonStatus>; restart(): Promise<DaemonStatus>; stop(): Promise<DaemonStatus>; repair(): Promise<DaemonStatus> };
  app: {
    info(): AppInfo;
    openExternal(url: string): Promise<void>;
    pickFolder(purpose: 'source' | 'skill-import'): Promise<string | null>;
    revealLogs(): Promise<void>;
    saveFile(name: string, data: Uint8Array): Promise<boolean>;
    openMain(route?: string): void;
    settings(): AppSettings;
    updateSettings(patch: AppSettingsPatch): AppSettings;
  };
};

type In<C extends Channel> = z.output<(typeof channels)[C]>;
type HandlerMap = { [C in Channel]: (input: In<C>, ctx: HandlerContext) => unknown };

const scope = (i: { projectId?: string | undefined }) => (i.projectId ? { projectId: i.projectId } : {});
const ok = { ok: true as const };

function safeExternalUrl(raw: string): string {
  let url: URL;
  try {
    url = new URL(raw);
  } catch {
    throw new UserFacingError('invalid_url', 'That is not a valid link.');
  }
  if (!['http:', 'https:', 'mailto:'].includes(url.protocol)) throw new UserFacingError('invalid_url', 'Only web and mail links can be opened.');
  return url.toString();
}

export const handlers = {
  health: (_i, c) => c.client().health(),
  overview: (_i, c) => c.client().overview(),
  usage: (i, c) => c.client().usage(i.since),

  'projects.list': (i, c) => c.client().projects.list(i.all ?? false),
  'projects.create': (i, c) => c.client().projects.create(i),
  'projects.get': (i, c) => c.client().projects.get(i.id),
  'projects.update': (i, c) => c.client().projects.update(i.id, i.patch),
  'projects.archive': (i, c) => c.client().projects.archive(i.id),
  'projects.addSource': (i, c) => c.client().projects.addSource(i.id, i.source),
  'projects.removeSource': (i, c) => c.client().projects.removeSource(i.id, i.sourceId),
  'projects.send': (i, c) => c.client().projects.send(i.id, i.text),
  'projects.chat': (i, c) => c.client().projects.chat(i.id, { ...(i.after !== undefined ? { after: i.after } : {}), ...(i.limit ? { limit: i.limit } : {}) }),
  'projects.plan': (i, c) => c.client().projects.plan(i.id),
  'projects.usage': (i, c) => c.client().projects.usage(i.id),
  'projects.events': (i, c) =>
    c.client().projects.events(i.id, {
      ...(i.after !== undefined ? { after: i.after } : {}),
      ...(i.limit ? { limit: i.limit } : {}),
      ...(i.types ? { types: i.types as never } : {}),
    }),

  'threads.list': (i, c) => c.client().threads.list(i.projectId, i.all ?? false),
  'threads.get': (i, c) => c.client().threads.get(i.id),
  'threads.transcript': (i, c) => c.client().threads.transcript(i.id, { ...(i.after !== undefined ? { after: i.after } : {}), ...(i.limit ? { limit: i.limit } : {}) }),
  'threads.send': (i, c) => c.client().threads.send(i.id, i.text),
  'threads.stop': (i, c) => c.client().threads.stop(i.id),
  'threads.archive': (i, c) => c.client().threads.archive(i.id),
  'threads.diff': (i, c) => c.client().threads.diff(i.id),
  'threads.files': (i, c) => c.client().threads.files(i.id, i.path ?? ''),
  'threads.file': (i, c) => c.client().threads.file(i.id, i.path),

  'approvals.list': (i, c) => c.client().approvals.list(i.projectId, i.status),
  'approvals.resolve': (i, c) => c.client().approvals.resolve(i.id, i.decision, i.note),

  'attention.list': (i, c) => c.client().attention.list(i.projectId),
  'attention.dismiss': (i, c) => c.client().attention.dismiss(i.id),

  'memory.list': (i, c) => c.client().memory.list(i.projectId, i.q),
  'memory.add': (i, c) => c.client().memory.add(i.projectId, i.entry),
  'memory.correct': (i, c) => c.client().memory.correct(i.projectId, i.memoryId, i.update),
  'memory.remove': (i, c) => c.client().memory.remove(i.projectId, i.memoryId),

  'library.list': (i, c) => c.client().library.list(i.projectId),
  'library.upload': (i, c) => c.client().library.upload(i.projectId, i.file),
  'library.file': (i, c) => c.client().library.file(i.projectId, i.path),

  'skills.list': (i, c) => c.client().skills.list(scope(i)),
  'skills.get': (i, c) => c.client().skills.get(scope(i), i.name),
  'skills.file': (i, c) => c.client().skills.file(scope(i), i.name, i.path),
  'skills.save': (i, c) => c.client().skills.save(scope(i), i.name, i.skill),
  'skills.remove': (i, c) => c.client().skills.remove(scope(i), i.name),
  'skills.history': (i, c) => c.client().skills.history(scope(i), i.name),
  'skills.restore': (i, c) => c.client().skills.restore(scope(i), i.name, i.version),
  'skills.import': (i, c) => c.client().skills.import(scope(i), { path: i.path, ...(i.name ? { name: i.name } : {}) }),
  'skills.version': (i, c) => c.client().skills.version(scope(i), i.name, i.version),
  'skills.versionFile': (i, c) => c.client().skills.versionFile(scope(i), i.name, i.version, i.path),

  'models.list': (_i, c) => c.client().models.list(),
  'models.replace': (i, c) => c.client().models.replace(i.models),

  'config.get': (_i, c) => c.client().config.get(),
  'config.patch': (i, c) => c.client().config.patch(i),
  'config.endpoint': (_i, c) => c.client().config.endpoint(),
  'config.saveEndpoint': (i, c) => c.client().config.saveEndpoint(i),
  'config.testEndpoint': (i, c) => c.client().config.testEndpoint(i.candidate),

  'broker.snapshot': (_i, c) => c.broker.snapshot(),
  'broker.watch': async (i, c) => {
    await c.broker.watch(c.senderId, i.projectId, i.afterSeq);
    return ok;
  },
  'broker.unwatch': (i, c) => {
    c.broker.unwatch(c.senderId, i.projectId);
    return ok;
  },

  'daemon.status': (_i, c) => c.daemon.status(),
  'daemon.start': (_i, c) => c.daemon.start(),
  'daemon.restart': (_i, c) => c.daemon.restart(),
  'daemon.stop': (_i, c) => c.daemon.stop(),
  'daemon.repair': (_i, c) => c.daemon.repair(),

  'app.info': (_i, c) => c.app.info(),
  'app.openExternal': async (i, c) => {
    await c.app.openExternal(safeExternalUrl(i.url));
    return ok;
  },
  'app.pickFolder': (i, c) => c.app.pickFolder(i.purpose),
  'app.revealLogs': async (_i, c) => {
    await c.app.revealLogs();
    return ok;
  },
  'app.openMain': (i, c) => {
    c.app.openMain(i.route);
    return { ok: true as const };
  },
  'app.saveFile': (i, c) => c.app.saveFile(i.name, i.data),
  'app.settings': (_i, c) => c.app.settings(),
  'app.updateSettings': (i, c) => c.app.updateSettings(i),
} satisfies HandlerMap;

export type ChannelOutput<C extends Channel> = Awaited<ReturnType<(typeof handlers)[C]>>;

/** Maps any failure to what the renderer may see: daemon codes pass through, internals become a generic message. */
export function toIpcError(err: unknown, log?: (err: unknown) => void): IpcError {
  if (err instanceof ApiError) return { code: err.code, message: err.message, status: err.status };
  if (err instanceof UserFacingError) return { code: err.code, message: err.message };
  if (err instanceof DaemonNotRunning) return { code: 'daemon_not_running', message: 'Desk is not running.' };
  if (err instanceof DaemonUnavailable) return { code: 'daemon_unavailable', message: 'Cannot reach Desk.' };
  if (err instanceof ProtocolMismatch) return { code: 'protocol_mismatch', message: err.message };
  log?.(err);
  return { code: 'internal', message: 'Something went wrong. See the logs for details.' };
}

/** Validates and runs one renderer call. Never throws. */
export async function dispatch(channel: unknown, input: unknown, ctx: HandlerContext, log?: (err: unknown) => void): Promise<IpcResult<unknown>> {
  if (typeof channel !== 'string' || !Object.hasOwn(channels, channel)) {
    return { ok: false, error: { code: 'unknown_channel', message: `Unknown operation: ${String(channel).slice(0, 64)}` } };
  }
  const c = channel as Channel;
  const parsed = channels[c].safeParse(input ?? {});
  if (!parsed.success) return { ok: false, error: { code: 'invalid_request', message: z.prettifyError(parsed.error).slice(0, 500) } };
  try {
    const run = handlers[c] as (i: unknown, ctx: HandlerContext) => unknown;
    return { ok: true, value: await run(parsed.data, ctx) };
  } catch (err) {
    return { ok: false, error: toIpcError(err, log) };
  }
}
