import { z } from 'zod';
import {
  AddSourceRequest,
  CreateProjectRequest,
  DaemonConfigPatch,
  LibraryUploadRequest,
  MemoryUpdateRequest,
  MemoryWriteRequest,
  ModelEndpointPutRequest,
  ModelsPutRequest,
  SkillWriteRequest,
  UpdateProjectRequest,
} from '@desk/protocol';

const id = z.string().min(1).max(200);
const text = z.string().min(1).max(100_000);
const relPath = z.string().min(1).max(4096);
const name = z.string().min(1).max(64);
const version = z.number().int().min(1);
const after = z.number().int().min(0).optional();
const limit = z.number().int().min(1).max(5000).optional();
const scope = { projectId: id.optional() };
const none = z.object({});

export const AppSettings = z.object({ notifications: z.boolean() });
export type AppSettings = z.infer<typeof AppSettings>;
export const AppSettingsPatch = AppSettings.partial();
export type AppSettingsPatch = z.input<typeof AppSettingsPatch>;

/** Every renderer → main operation and its payload schema. Validated in main before anything runs. */
export const channels = {
  health: none,
  overview: none,
  usage: z.object({ since: z.string().max(64).optional() }),

  'projects.list': z.object({ all: z.boolean().optional() }),
  'projects.create': CreateProjectRequest,
  'projects.get': z.object({ id }),
  'projects.update': z.object({ id, patch: UpdateProjectRequest }),
  'projects.archive': z.object({ id }),
  'projects.addSource': z.object({ id, source: AddSourceRequest }),
  'projects.removeSource': z.object({ id, sourceId: id }),
  'projects.send': z.object({ id, text }),
  'projects.chat': z.object({ id, after, limit }),
  'projects.plan': z.object({ id }),
  'projects.usage': z.object({ id }),
  'projects.events': z.object({ id, after, limit, types: z.array(z.string().max(64)).max(64).optional() }),

  'threads.list': z.object({ projectId: id, all: z.boolean().optional() }),
  'threads.get': z.object({ id }),
  'threads.transcript': z.object({ id, after, limit }),
  'threads.send': z.object({ id, text }),
  'threads.stop': z.object({ id }),
  'threads.archive': z.object({ id }),
  'threads.diff': z.object({ id }),
  'threads.files': z.object({ id, path: z.string().max(4096).optional() }),
  'threads.file': z.object({ id, path: relPath }),

  'approvals.list': z.object({ projectId: id, status: z.enum(['pending', 'approved', 'denied']).optional() }),
  'approvals.resolve': z.object({ id, decision: z.enum(['approved', 'denied']), note: z.string().max(2000).optional() }),

  'attention.list': z.object({ projectId: id.optional() }),
  'attention.dismiss': z.object({ id: z.string().min(1).max(400) }),

  'memory.list': z.object({ projectId: id, q: z.string().max(500).optional() }),
  'memory.add': z.object({ projectId: id, entry: MemoryWriteRequest }),
  'memory.correct': z.object({ projectId: id, memoryId: id, update: MemoryUpdateRequest }),
  'memory.remove': z.object({ projectId: id, memoryId: id }),

  'library.list': z.object({ projectId: id }),
  'library.upload': z.object({ projectId: id, file: LibraryUploadRequest }),
  'library.file': z.object({ projectId: id, path: relPath }),

  'skills.list': z.object(scope),
  'skills.get': z.object({ ...scope, name }),
  'skills.file': z.object({ ...scope, name, path: relPath }),
  'skills.save': z.object({ ...scope, name, skill: SkillWriteRequest }),
  'skills.remove': z.object({ ...scope, name }),
  'skills.history': z.object({ ...scope, name }),
  'skills.restore': z.object({ ...scope, name, version }),
  'skills.import': z.object({ ...scope, path: z.string().min(1).max(4096), name: name.optional() }),
  'skills.version': z.object({ ...scope, name, version }),
  'skills.versionFile': z.object({ ...scope, name, version, path: relPath }),

  'models.list': none,
  'models.replace': z.object({ models: ModelsPutRequest }),

  'config.get': none,
  'config.patch': DaemonConfigPatch,
  'config.endpoint': none,
  'config.saveEndpoint': ModelEndpointPutRequest,
  'config.testEndpoint': z.object({ candidate: ModelEndpointPutRequest.optional() }),

  'broker.snapshot': none,
  'broker.watch': z.object({ projectId: id, afterSeq: z.number().int().min(0) }),
  'broker.unwatch': z.object({ projectId: id }),

  'daemon.status': none,
  'daemon.start': none,
  'daemon.restart': none,
  'daemon.stop': none,

  'app.info': none,
  'app.openExternal': z.object({ url: z.string().min(1).max(4096) }),
  'app.pickFolder': z.object({ purpose: z.enum(['source', 'skill-import']) }),
  'app.revealLogs': none,
  /** From the tray popover: bring up the main window, optionally at a hash route. */
  'app.openMain': z.object({ route: z.string().max(512).regex(/^#\/[^\s]*$/).optional() }),
  'app.saveFile': z.object({ name: z.string().min(1).max(255), data: z.instanceof(Uint8Array) }),
  'app.settings': none,
  'app.updateSettings': AppSettingsPatch,
} as const;

export type Channel = keyof typeof channels;
export type ChannelInput<C extends Channel> = z.input<(typeof channels)[C]>;
export type IpcError = { code: string; message: string; status?: number };
export type IpcResult<T> = { ok: true; value: T } | { ok: false; error: IpcError };
