import { describe, expect, it } from 'vitest';
import type { BuiltinSkillInfo } from '@desk/protocol';
import { builtinKey, parseBuiltinKey, runtimeLabel } from './data';

const b = (state: BuiltinSkillInfo['runtime']['state'], reason: string | null = null): BuiltinSkillInfo => ({
  name: 'pdf-toolkit',
  title: 'PDF toolkit',
  summary: '',
  caveats: [],
  description: '',
  scripts: 1,
  enabled: true,
  broken: null,
  shadowed_by: null,
  runtime: { state, reason },
});

describe('built-in skill helpers', () => {
  it('round-trips keys', () => {
    expect(parseBuiltinKey(builtinKey('pdf-toolkit'))).toBe('pdf-toolkit');
    expect(parseBuiltinKey('global:pdf-toolkit')).toBeNull();
    expect(parseBuiltinKey('builtin:')).toBeNull();
  });

  it('words the environment state', () => {
    expect(runtimeLabel(b('none'))).toBe('Set up on first use');
    expect(runtimeLabel(b('preparing'), { step: 'Installing 6 Python packages' })).toBe('Setting up… Installing 6 Python packages');
    expect(runtimeLabel(b('preparing'))).toBe('Setting up…');
    expect(runtimeLabel(b('ready'))).toBe('Ready');
    expect(runtimeLabel(b('failed', 'no network'))).toBe('Setup failed: no network');
  });
});
