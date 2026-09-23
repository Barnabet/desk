import { z } from 'zod';

export const PolicyRule = z.object({
  tool: z.string().min(1),
  match: z
    .object({
      branch: z.string().optional(),
      command: z.string().optional(),
      domain: z.string().optional(),
    })
    .optional(),
  action: z.enum(['allow', 'ask', 'deny']),
  delegate_to_desk: z.boolean().optional(),
});
export type PolicyRule = z.infer<typeof PolicyRule>;

/** Shell commands that always need a human (or Desk) decision, even inside the sandbox. */
export const RISKY_COMMAND_PATTERN =
  String.raw`(?:^|[\s;&|(])(?:(?:sudo|mkfs(?:\.\w+)?)\b|dd\s+if=|chmod\s+-R\s+777|rm\s+-\w*[rR]\w*\s+(?:/|~))|(?:curl|wget)[^|]*\|\s*(?:ba|z)?sh\b`;

export const DEFAULT_POLICY: PolicyRule[] = [
  { tool: 'bash', match: { command: RISKY_COMMAND_PATTERN }, action: 'ask' },
  { tool: 'bash_background', match: { command: RISKY_COMMAND_PATTERN }, action: 'ask' },
  { tool: 'bash_readonly', match: { command: RISKY_COMMAND_PATTERN }, action: 'ask' },
  { tool: 'skill_run', match: { command: RISKY_COMMAND_PATTERN }, action: 'ask' },
  { tool: 'git_push', match: { branch: 'desk/*' }, action: 'allow' },
  { tool: 'git_push', action: 'deny' },
  { tool: 'open_pr', action: 'ask', delegate_to_desk: false },
  { tool: 'web_fetch', action: 'allow' },
  { tool: 'web_search', action: 'allow' },
];

export const ProjectSettings = z.object({
  desk_model: z.string().min(1).default('claude-opus-5-5'),
  thread_model: z.string().min(1).default('claude-opus-5-5'),
  fallback_model: z.string().min(1).nullable().default(null),
  max_concurrent_threads: z.number().int().min(1).max(32).default(4),
  check_in: z.enum(['minimal', 'normal', 'detailed']).default('normal'),
  autonomy: z.enum(['dispatch-freely', 'ask-before-dispatch']).default('dispatch-freely'),
  review_rounds: z.number().int().min(0).max(10).default(2),
  policy: z.array(PolicyRule).default(() => DEFAULT_POLICY.map((r) => ({ ...r }))),
});
export type ProjectSettings = z.infer<typeof ProjectSettings>;

/** Input-side settings: every field optional (a patch). */
export const ProjectSettingsPatch = ProjectSettings.partial();
export type ProjectSettingsPatch = z.input<typeof ProjectSettingsPatch>;

export function resolveSettings(partial: ProjectSettingsPatch = {}): ProjectSettings {
  return ProjectSettings.parse(partial);
}
