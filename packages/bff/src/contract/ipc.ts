import { z } from 'zod';
import {
  AddSourceRequest,
  AutomationAnswerRequest,
  AutomationCreateRequest,
  AutomationImportRequest,
  AutomationLayout,
  AutomationRunRequest,
  AutomationSaveRequest,
  AutomationValidateRequest,
  CreateProjectRequest,
  DaemonConfigPatch,
  Grant,
  LibraryUploadRequest,
  MemoryUpdateRequest,
  MemoryWriteRequest,
  ModelEndpointPutRequest,
  ModelsPutRequest,
  SkillWriteRequest,
  StepId,
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
/** Run ids are ULIDs; `app.revealPath` builds a filesystem path from one, so nothing else passes. */
const RUN_ID = /^[0-9A-HJKMNP-TV-Z]{26}$/;

/** `system` follows macOS; main maps it to `nativeTheme.themeSource`, which every window's CSS sees as prefers-color-scheme. */
export const Appearance = z.enum(['system', 'light', 'dark']);
export type Appearance = z.infer<typeof Appearance>;
export const AppSettings = z.object({ notifications: z.boolean(), appearance: Appearance });
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
  'projects.setSourceWrite': z.object({ id, sourceId: id, agentWrite: z.boolean() }),
  'projects.send': z.object({ id, text }),
  'projects.chat': z.object({ id, after, limit }),
  'projects.plan': z.object({ id }),
  'projects.usage': z.object({ id }),
  'projects.events': z.object({ id, after, limit, types: z.array(z.string().max(64)).max(64).optional() }),

  'threads.list': z.object({ projectId: id, all: z.boolean().optional() }),
  'threads.get': z.object({ id }),
  'threads.transcript': z.object({ id, after, limit }),
  /** A steer, or with `question` the user's Ask: a done, failed or idle thread answers it without reopening. */
  'threads.send': z.object({ id, text, question: z.boolean().optional() }),
  'threads.stop': z.object({ id }),
  'threads.archive': z.object({ id }),
  'threads.diff': z.object({ id }),
  'services.logs': z.object({ id, lines: z.number().int().min(1).max(2000).optional() }),
  'services.start': z.object({ id }),
  'services.stop': z.object({ id }),
  'services.restart': z.object({ id }),
  'threads.files': z.object({ id, path: z.string().max(4096).optional() }),
  'threads.file': z.object({ id, path: relPath }),

  'approvals.list': z.object({ projectId: id, status: z.enum(['pending', 'approved', 'denied']).optional() }),
  /** `remember` (a step agent's approval only): also adds a grant to its automation (spec §5.3). */
  'approvals.resolve': z.object({ id, decision: z.enum(['approved', 'denied']), note: z.string().max(2000).optional(), remember: z.boolean().optional() }),

  'attention.list': z.object({ projectId: id.optional() }),
  'attention.dismiss': z.object({ id: z.string().min(1).max(400) }),

  'automations.list': z.object({ projectId: id }),
  'automations.create': z.object({ projectId: id, req: AutomationCreateRequest }),
  'automations.validate': z.object({ projectId: id, req: AutomationValidateRequest }),
  'automations.import': z.object({ projectId: id, exp: AutomationImportRequest }),
  'automations.get': z.object({ id }),
  'automations.save': z.object({ id, req: AutomationSaveRequest }),
  'automations.remove': z.object({ id }),
  'automations.layout': z.object({ id, layout: AutomationLayout }),
  'automations.versions': z.object({ id }),
  'automations.version': z.object({ id, version }),
  'automations.restore': z.object({ id, version }),
  'automations.setEnabled': z.object({ id, enabled: z.boolean() }),
  'automations.setGrants': z.object({ id, grants: z.array(Grant).max(100), reason: z.enum(['edited', 'enabled']).optional() }),
  'automations.keepGrants': z.object({ id }),
  'automations.export': z.object({ id }),
  'automations.run': z.object({ id, req: AutomationRunRequest }),
  'automations.runs': z.object({ id, before: z.string().max(64).optional(), limit: z.number().int().min(1).max(200).optional() }),
  'automations.getRun': z.object({ runId: id }),
  'automations.cancelRun': z.object({ runId: id }),
  'automations.answer': z.object({ runId: id, stepId: StepId, req: AutomationAnswerRequest }),
  'automations.stopStep': z.object({ runId: id, stepId: StepId }),
  'automations.log': z.object({ runId: id, stepId: StepId }),
  'automations.files': z.object({ runId: id, path: z.string().max(4096).optional() }),
  'automations.file': z.object({ runId: id, path: relPath }),

  'memory.list': z.object({ projectId: id, q: z.string().max(500).optional() }),
  'memory.add': z.object({ projectId: id, entry: MemoryWriteRequest }),
  'memory.correct': z.object({ projectId: id, memoryId: id, update: MemoryUpdateRequest }),
  'memory.remove': z.object({ projectId: id, memoryId: id }),

  /** An image an agent looked at (view_image), as a data URL (the renderer scales thumbnails itself). */
  'attachments.get': z.object({ sha256: z.string().regex(/^[0-9a-f]{64}$/) }),

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
  'skills.runtimeRetry': z.object({ ...scope, name }),

  'builtins.list': z.object({ projectId: id.optional() }),
  'builtins.get': z.object({ name }),
  'builtins.file': z.object({ name, path: relPath }),
  'builtins.setEnabled': z.object({ name, enabled: z.boolean() }),
  'builtins.duplicate': z.object({ name, projectId: id.optional() }),
  'builtins.retry': z.object({ name }),

  'catalog.list': none,
  'catalog.prepare': z.object({ id: name }),
  'catalog.file': z.object({ id: name, path: relPath }),
  'catalog.install': z.object({ id: name, projectId: id.optional(), replaceModified: z.boolean().optional() }),

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

  'system.runtimes': none,
  'system.runtimesCleanup': none,
  'system.workspaces': none,
  'system.workspacesCleanup': none,

  'daemon.status': none,
  'daemon.start': none,
  'daemon.restart': none,
  'daemon.stop': none,
  /** Reinstall the LaunchAgent (packaged macOS); a restart in dev; desk web answers not_offered. */
  'daemon.repair': none,

  'app.info': none,
  'app.openExternal': z.object({ url: z.string().min(1).max(4096) }),
  'app.pickFolder': z.object({ purpose: z.enum(['source', 'skill-import', 'automation-input']) }),
  /** A file for a Run now / Test input: deskd copies it into the run folder. desk web answers not_offered. */
  'app.pickFile': z.object({ purpose: z.enum(['automation-input']) }),
  /** Opens a run's folder, or one step's folder in it, with the platform opener. Nothing else can be revealed. */
  'app.revealPath': z.object({ runId: z.string().regex(RUN_ID), stepId: StepId.optional() }),
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
