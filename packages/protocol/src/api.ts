import { z } from 'zod';
import { ArtifactKind, MemoryKind, ModelInfo, SkillName } from './domain';
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

export const AddSourceRequest = z.object({ path: z.string().min(1), label: z.string().optional() });
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

export const HealthResponse = z.object({ version: z.string(), protocol_version: z.number().int() });
export type HealthResponse = z.infer<typeof HealthResponse>;

export const ErrorResponse = z.object({ error: z.object({ code: z.string(), message: z.string(), details: z.unknown().optional() }) });
export type ErrorResponse = z.infer<typeof ErrorResponse>;

/** Client → server on the /v1/stream WebSocket. `project_id` may be '*' for every project. */
export const StreamClientMessage = z.object({
  subscribe: z.object({ project_id: z.string().min(1), after_seq: z.number().int().min(0) }),
});
export type StreamClientMessage = z.infer<typeof StreamClientMessage>;

/** Server → client. Persisted events are replayed after the cursor, then `ready`, then live events. */
export type StreamServerMessage =
  | { kind: 'event'; event: import('./events').StoredEvent }
  | { kind: 'ephemeral'; event: EphemeralEvent }
  | { kind: 'ready'; seq: number }
  | { kind: 'error'; message: string };
