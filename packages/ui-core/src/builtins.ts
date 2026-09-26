import type { BuiltinSkillInfo } from '@desk/protocol';

/** The skills screen's selection key for a built-in skill (user skills use `global:` and `project:` keys). */
export const builtinKey = (name: string) => `builtin:${name}`;
export const parseBuiltinKey = (key: string): string | null => (key.startsWith('builtin:') && key.length > 8 ? key.slice(8) : null);

/** The environment line of a built-in skill card. */
export function runtimeLabel(b: BuiltinSkillInfo, progress?: { step: string } | null): string {
  switch (b.runtime.state) {
    case 'none':
      return 'Set up on first use';
    case 'preparing':
      return `Setting up…${progress ? ` ${progress.step}` : ''}`;
    case 'ready':
      return 'Ready';
    default:
      return `Setup failed${b.runtime.reason ? `: ${b.runtime.reason}` : ''}`;
  }
}
