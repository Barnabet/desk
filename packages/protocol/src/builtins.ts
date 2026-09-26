import { z } from 'zod';
import { CatalogRuntime, RuntimeState } from './catalog';
import { SkillName, WritableSkillScope } from './domain';

/** One of Desk's own skills as its manifest records it (spec 2026-09-26-builtin-skills-design §1). */
export const BuiltinEntry = z.object({
  name: SkillName,
  title: z.string().min(1).max(80),
  summary: z.string().min(1).max(200),
  caveats: z.array(z.string()).default([]),
  digest: z.string().regex(/^sha256:[0-9a-f]{64}$/),
  files: z.number().int().min(1),
  bytes: z.number().int().min(1),
  scripts: z.number().int().min(0),
  runtime: CatalogRuntime.default({}),
  /** Command run by `catalog:check --builtins` inside the sandbox, from the skill directory. */
  smoke: z.array(z.string()).optional(),
});
export type BuiltinEntry = z.infer<typeof BuiltinEntry>;

export const BuiltinsFile = z.object({
  version: z.literal(1),
  /** Pin date; also the `--exclude-newer` bound for their Python packages. */
  updated: z.string().regex(/^\d{4}-\d{2}-\d{2}$/),
  skills: z.array(BuiltinEntry),
});
export type BuiltinsFile = z.infer<typeof BuiltinsFile>;

/** A built-in skill as clients see it (GET /v1/builtin-skills). */
export const BuiltinSkillInfo = z.object({
  name: SkillName,
  title: z.string(),
  summary: z.string(),
  caveats: z.array(z.string()),
  description: z.string(),
  scripts: z.number().int(),
  enabled: z.boolean(),
  /** Why the shipped copy cannot be used (digest mismatch, unreadable), or null. */
  broken: z.string().nullable(),
  /** The user's own skill of the same name that agents use instead, if any. */
  shadowed_by: WritableSkillScope.nullable(),
  runtime: z.object({ state: RuntimeState, reason: z.string().nullable() }),
});
export type BuiltinSkillInfo = z.infer<typeof BuiltinSkillInfo>;

export const BuiltinToggleRequest = z.object({ enabled: z.boolean() });
export type BuiltinToggleRequest = z.infer<typeof BuiltinToggleRequest>;

export const BuiltinDuplicateRequest = z
  .object({ scope: WritableSkillScope.default('global'), project_id: z.string().min(1).optional() })
  .refine((r) => r.scope !== 'project' || !!r.project_id, { message: 'A project copy needs project_id', path: ['project_id'] });
export type BuiltinDuplicateRequest = z.input<typeof BuiltinDuplicateRequest>;
