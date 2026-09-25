import { z } from 'zod';
import { AgentStatus, ArtifactKind, MemoryKind, ModelInfo, SkillName } from './domain';
import { EphemeralEvent } from './events';
import { ProjectSettingsPatch } from './settings';

export const CreateProjectRequest = z.object({
  name: z.string().min(1),
  goal: z.string().default(''),
  instructions: z.string().optional(),
  settings: ProjectSettingsPatch.optional(),
  sources: z.array(z.object({ path: z.string().min(1), label: z.string().optional() })).optional(),
});
export type CreateProjectRequest = z.input<typeof CreateProjectRequest>;

export const UpdateProjectRequest = z.object({
  name: z.string().min(1).optional(),
  goal: z.string().optional(),
  instructions: z.string().optional(),
  settings: ProjectSettingsPatch.optional(),
});
export type UpdateProjectRequest = z.input<typeof UpdateProjectRequest>;

export const AddSourceRequest = z.object({ path: z.string().min(1), label: z.string().optional(), agent_write: z.boolean().optional() });
export const UpdateSourceRequest = z.object({ agent_write: z.boolean() });
export type UpdateSourceRequest = z.input<typeof UpdateSourceRequest>;
export type AddSourceRequest = z.input<typeof AddSourceRequest>;

export const MessageRequest = z.object({ text: z.string().min(1) });
export type MessageRequest = z.input<typeof MessageRequest>;

export const ResolveApprovalRequest = z.object({ decision: z.enum(['approved', 'denied']), note: z.string().optional() });
export type ResolveApprovalRequest = z.input<typeof ResolveApprovalRequest>;

export const MemoryWriteRequest = z.object({ kind: MemoryKind, content: z.string().min(1), supersedes: z.string().optional() });
export type MemoryWriteRequest = z.input<typeof MemoryWriteRequest>;

/** Replaces an entry: writes a new one that supersedes it. */
export const MemoryUpdateRequest = z.object({ content: z.string().min(1), kind: MemoryKind.optional() });
export type MemoryUpdateRequest = z.input<typeof MemoryUpdateRequest>;

export const LibraryUploadRequest = z.object({
  name: z.string().min(1),
  content_base64: z.string(),
  title: z.string().optional(),
  kind: ArtifactKind.optional(),
  description: z.string().optional(),
});
export type LibraryUploadRequest = z.input<typeof LibraryUploadRequest>;

/** Create or refine a skill. For an update, omitted fields keep their value. `SKILL.md` may be sent as a file. */
export const SkillWriteRequest = z.object({
  description: z.string().min(1).max(1024).optional(),
  instructions: z.string().min(1).optional(),
  files: z.array(z.object({ path: z.string().min(1), content_base64: z.string() })).default([]),
  remove_files: z.array(z.string()).default([]),
  change_note: z.string().optional(),
});
export type SkillWriteRequest = z.input<typeof SkillWriteRequest>;

/** Import a skill directory from the local disk (e.g. a Claude Code skill). */
export const SkillImportRequest = z.object({ path: z.string().min(1), name: SkillName.optional() });
export type SkillImportRequest = z.input<typeof SkillImportRequest>;

export const SkillRestoreRequest = z.object({ version: z.number().int().min(1) });
export type SkillRestoreRequest = z.input<typeof SkillRestoreRequest>;

export const ModelsPutRequest = z.array(ModelInfo).min(1);
export type ModelsPutRequest = z.input<typeof ModelsPutRequest>;

export const HealthResponse = z.object({
  version: z.string(),
  protocol_version: z.number().int(),
  proxy: z.enum(['up', 'down', 'unknown']).optional(),
  uptime_s: z.number().int().min(0).optional(),
  /** The bundled deskd's content hash (`build-id` next to deskd.mjs); null when running from source. Absent before 1.0 builds had one. */
  build: z.string().nullable().optional(),
});
export type HealthResponse = z.infer<typeof HealthResponse>;

export const ErrorResponse = z.object({ error: z.object({ code: z.string(), message: z.string(), details: z.unknown().optional() }) });
export type ErrorResponse = z.infer<typeof ErrorResponse>;

/** Client → server on the /v1/stream WebSocket. `project_id` may be '*' for every project. */
export const StreamClientMessage = z.union([
  z.object({ subscribe: z.object({ project_id: z.string().min(1), after_seq: z.number().int().min(0) }) }),
  /** Identifies the client; a desktop client that shows notifications silences the daemon's own notifier. */
  z.object({ hello: z.object({ client: z.string().min(1).max(64), notifications: z.boolean().default(false) }) }),
]);
export type StreamClientMessage = z.infer<typeof StreamClientMessage>;

/** Server → client. Persisted events are replayed after the cursor, then `ready`, then live events. */
export type StreamServerMessage =
  | { kind: 'event'; event: import('./events').StoredEvent }
  | { kind: 'ephemeral'; event: EphemeralEvent }
  | { kind: 'ready'; seq: number }
  | { kind: 'error'; message: string };

// ── UI endpoints ─────────────────────────────────────────────────────

export const AttentionKind = z.enum(['approval', 'question', 'needs_you', 'stalled', 'failed', 'paused']);
export type AttentionKind = z.infer<typeof AttentionKind>;

/** The `system.notice` code of a project whose automatic wakes the runtime paused (design spec §5.4). */
export const WAKES_PAUSED = 'wakes_paused';

/** One thing that needs the user. `id` is stable: `approval:<id>`, `question:<event>`, `report:<event>:<i>`, `stalled:<thread>:<event>`, `failed:<thread>`, `paused:<notice event>`. */
export const AttentionItem = z.object({
  id: z.string(),
  kind: AttentionKind,
  project_id: z.string(),
  project_name: z.string(),
  agent_id: z.string().nullable(),
  title: z.string(),
  detail: z.string(),
  created_at: z.string(),
  ref: z.object({
    approval_id: z.string().optional(),
    event_id: z.number().int().optional(),
    thread_id: z.string().optional(),
    options: z.array(z.string()).optional(),
  }),
});
export type AttentionItem = z.infer<typeof AttentionItem>;

export const AttentionResponse = z.object({ items: z.array(AttentionItem), seq: z.number().int() });
export type AttentionResponse = z.infer<typeof AttentionResponse>;

export const OverviewThread = z.object({
  id: z.string(),
  title: z.string().nullable(),
  status: AgentStatus,
  reason: z.string().nullable(),
  /** The tool call in flight, e.g. `bash · python3 funnel.py`; null when none. */
  activity: z.string().nullable(),
  model: z.string(),
  git_branch: z.string().nullable(),
  skills: z.array(z.string()),
  review_round: z.number().int(),
  created_at: z.string(),
  updated_at: z.string(),
});
export type OverviewThread = z.infer<typeof OverviewThread>;

export const ProjectSummary = z.object({
  project: z.object({ id: z.string(), name: z.string(), goal: z.string(), updated_at: z.string() }),
  desk_status: AgentStatus,
  threads: z.array(OverviewThread),
  latest_report: z.object({ headline: z.string(), ts: z.string() }).nullable(),
  plan_progress: z.object({ done: z.number().int(), total: z.number().int() }),
  attention_count: z.number().int(),
});
export type ProjectSummary = z.infer<typeof ProjectSummary>;

export const DiffFileStatus = z.enum(['added', 'modified', 'deleted']);
export const ThreadDiff = z.object({
  base: z.string(),
  branch: z.string(),
  files: z.array(z.object({ path: z.string(), status: DiffFileStatus, additions: z.number().int().nullable(), deletions: z.number().int().nullable() })),
  patch: z.string(),
});
export type ThreadDiff = z.infer<typeof ThreadDiff>;

export const WorkspaceEntry = z.object({ name: z.string(), path: z.string(), type: z.enum(['file', 'dir']), size: z.number().int() });
export type WorkspaceEntry = z.infer<typeof WorkspaceEntry>;

export const UsageResponse = z.object({
  rows: z.array(z.object({ project_id: z.string(), model: z.string(), prompt_tokens: z.number().int(), completion_tokens: z.number().int() })),
  totals: z.object({ prompt_tokens: z.number().int(), completion_tokens: z.number().int() }),
});
export type UsageResponse = z.infer<typeof UsageResponse>;

export const ModelEndpointStatus = z.object({
  configured: z.boolean(),
  source: z.enum(['env', 'file', 'keychain']).nullable(),
  base_url: z.string().nullable(),
});
export type ModelEndpointStatus = z.infer<typeof ModelEndpointStatus>;

/** Printable ASCII, no whitespace, quotes or backslashes (keys travel through `security -i`). */
const ApiKey = z
  .string()
  .min(1)
  .max(512)
  .regex(/^[\x21-\x7E]+$/, 'API keys are printable ASCII without spaces')
  .refine((k) => !/["'\\]/.test(k), 'API keys cannot contain quotes or backslashes');

export const ModelEndpointPutRequest = z.object({ base_url: z.url(), api_key: ApiKey });
export type ModelEndpointPutRequest = z.input<typeof ModelEndpointPutRequest>;

/** Test a candidate endpoint before saving it, or the current one when omitted. */
export const ModelEndpointTestRequest = ModelEndpointPutRequest.optional();
export type ModelEndpointTestRequest = z.input<typeof ModelEndpointTestRequest>;

export const ModelEndpointTestResult = z.object({ ok: z.boolean(), models: z.array(z.string()).optional(), error: z.string().optional() });
export type ModelEndpointTestResult = z.infer<typeof ModelEndpointTestResult>;

export const DaemonConfig = z.object({ notifications: z.enum(['auto', 'off']) });
export type DaemonConfig = z.infer<typeof DaemonConfig>;
export const DaemonConfigPatch = DaemonConfig.partial();
export type DaemonConfigPatch = z.input<typeof DaemonConfigPatch>;
