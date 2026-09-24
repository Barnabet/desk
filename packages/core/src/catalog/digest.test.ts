import { mkdirSync, mkdtempSync, rmSync, symlinkSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { describe, expect, it } from 'vitest';
import { readTree, treeDigest } from './digest';

const f = (path: string, s: string) => ({ path, content: Buffer.from(s) });

describe('treeDigest', () => {
  it('is independent of order and changes with any path or byte', () => {
    const a = treeDigest([f('SKILL.md', 'x'), f('scripts/a.py', 'print(1)')]);
    expect(a).toMatch(/^sha256:[0-9a-f]{64}$/);
    expect(treeDigest([f('scripts/a.py', 'print(1)'), f('SKILL.md', 'x')])).toBe(a);
    expect(treeDigest([f('SKILL.md', 'x'), f('scripts/b.py', 'print(1)')])).not.toBe(a);
    expect(treeDigest([f('SKILL.md', 'x'), f('scripts/a.py', 'print(2)')])).not.toBe(a);
    expect(treeDigest([f('SKILL.md', 'x')])).not.toBe(a);
  });

  it('matches a directory read from disk and refuses links', () => {
    const dir = mkdtempSync(join(tmpdir(), 'desk-digest-'));
    try {
      mkdirSync(join(dir, 'scripts'));
      writeFileSync(join(dir, 'SKILL.md'), 'x');
      writeFileSync(join(dir, 'scripts', 'a.py'), 'print(1)');
      expect(treeDigest(readTree(dir))).toBe(treeDigest([f('SKILL.md', 'x'), f('scripts/a.py', 'print(1)')]));
      symlinkSync('/etc/hosts', join(dir, 'hosts'));
      expect(() => readTree(dir)).toThrow(/symbolic link/);
    } finally {
      rmSync(dir, { recursive: true, force: true });
    }
  });
});
