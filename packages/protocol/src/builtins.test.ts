import { describe, expect, it } from 'vitest';
import { BuiltinDuplicateRequest, BuiltinsFile, BuiltinSkillInfo, EventBody, SkillScope, WritableSkillScope } from './index';

const entry = {
  name: 'pdf-toolkit',
  title: 'PDF toolkit',
  summary: 'Read and write PDFs. Written by Desk.',
  caveats: [],
  digest: `sha256:${'a'.repeat(64)}`,
  files: 3,
  bytes: 100,
  scripts: 2,
  runtime: { python: { version: '3.12', packages: ['pypdf==6.19.0'] } },
  smoke: ['python3', 'scripts/selftest.py'],
};

describe('built-in skill schemas', () => {
  it('adds the builtin scope, but only project and global are writable', () => {
    expect(SkillScope.parse('builtin')).toBe('builtin');
    expect(WritableSkillScope.safeParse('builtin').success).toBe(false);
  });

  it('parses a manifest', () => {
    const m = BuiltinsFile.parse({ version: 1, updated: '2026-09-26', skills: [entry] });
    expect(m.skills[0]!.runtime.python!.packages).toEqual(['pypdf==6.19.0']);
  });

  it('refuses a duplicate into a project without its id', () => {
    expect(BuiltinDuplicateRequest.safeParse({ scope: 'project' }).success).toBe(false);
    expect(BuiltinDuplicateRequest.parse({})).toEqual({ scope: 'global' });
  });

  it('describes a built-in for clients', () => {
    const info = BuiltinSkillInfo.parse({
      name: 'pdf-toolkit',
      title: 'PDF toolkit',
      summary: 's',
      caveats: [],
      description: 'd',
      scripts: 2,
      enabled: true,
      broken: null,
      shadowed_by: null,
      runtime: { state: 'none', reason: null },
    });
    expect(info.shadowed_by).toBeNull();
  });

  it('has a toggle event', () => {
    expect(EventBody.parse({ type: 'skill.builtin_toggled', payload: { name: 'pdf-toolkit', enabled: false } }).type).toBe('skill.builtin_toggled');
  });
});
