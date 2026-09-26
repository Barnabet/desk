import type { BuiltinSkillInfo } from '@desk/protocol';

/** A built-in skill for component tests; `over` replaces any field. */
export function builtin(name: string, over: Partial<BuiltinSkillInfo> = {}): BuiltinSkillInfo {
  const title = name.replace(/-/g, ' ').replace(/^./, (c) => c.toUpperCase());
  return {
    name,
    title,
    summary: `${title}: read, create and convert.`,
    caveats: [],
    description: `Use for ${name}.`,
    scripts: 4,
    enabled: true,
    broken: null,
    shadowed_by: null,
    runtime: { state: 'none', reason: null },
    ...over,
  };
}
