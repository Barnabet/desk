import { describe, expect, it } from 'vitest';
import { diffFiles, diffLines, withContext } from './diff';

describe('diffLines', () => {
  it('finds added and removed lines', () => {
    expect(diffLines('a\nb\nc', 'a\nc\nd')).toEqual([
      { kind: 'same', text: 'a' },
      { kind: 'del', text: 'b' },
      { kind: 'same', text: 'c' },
      { kind: 'add', text: 'd' },
    ]);
  });

  it('trims unchanged runs to context', () => {
    const before = Array.from({ length: 20 }, (_, i) => `l${i}`).join('\n');
    const after = before.replace('l10', 'L10');
    const shown = withContext(diffLines(before, after), 1);
    expect(shown.map((l) => (l ? `${l.kind}:${l.text}` : '…'))).toEqual(['same:l9', 'del:l10', 'add:L10', 'same:l11']);
  });
});

describe('diffFiles', () => {
  it('reports added, removed and resized files', () => {
    expect(diffFiles([{ path: 'SKILL.md', size: 10 }, { path: 'a.py', size: 3 }], [{ path: 'SKILL.md', size: 12 }, { path: 'b.py', size: 4 }])).toEqual([
      { path: 'SKILL.md', change: 'changed', before: 10, after: 12 },
      { path: 'a.py', change: 'removed', before: 3 },
      { path: 'b.py', change: 'added', after: 4 },
    ]);
  });
});
