import { mkdtempSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { describe, expect, it } from 'vitest';
import { BuiltinSkills, loadBuiltinsManifest } from './builtins';
import { SkillStore } from './store';

const SKILLS = join(import.meta.dirname, '..', '..', '..', '..', 'catalog', 'skills');

function setup(off: string[] = []) {
  const data = mkdtempSync(join(tmpdir(), 'desk-store-'));
  const b = new BuiltinSkills({ root: SKILLS, manifest: loadBuiltinsManifest(), enabled: (n) => !off.includes(n) });
  b.verify();
  return { data, store: new SkillStore(data, undefined, b) };
}

describe('SkillStore with built-in skills', () => {
  it('resolves project, then global, then built-in', () => {
    const { store } = setup();
    expect(store.resolve('pdf-toolkit', 'p1')?.scope).toBe('builtin');
    store.save({ scope: 'global', name: 'pdf-toolkit', description: 'mine', instructions: 'Do it my way.' });
    expect(store.resolve('pdf-toolkit', 'p1')?.scope).toBe('global');
    store.save({ scope: 'project', projectId: 'p1', name: 'pdf-toolkit', description: 'ours', instructions: 'Project way.' });
    expect(store.resolve('pdf-toolkit', 'p1')?.scope).toBe('project');
  });

  it('lists built-ins for agents only when asked, without shadowed or switched-off ones', () => {
    const { store } = setup(['images']);
    store.save({ scope: 'global', name: 'archives', description: 'mine', instructions: 'x' });
    expect(store.list().some((s) => s.scope === 'builtin')).toBe(false);
    const agentView = store.list(undefined, { builtins: true });
    expect(agentView.filter((s) => s.scope === 'builtin').map((s) => s.name)).not.toContain('images');
    expect(agentView.find((s) => s.name === 'archives')?.scope).toBe('global');
    expect(agentView.filter((s) => s.name === 'archives')).toHaveLength(1);
    expect(agentView.filter((s) => s.scope === 'builtin')).toHaveLength(10);
    expect(store.resolve('images')).toBeUndefined();
  });

  it('reads built-ins but never writes them', () => {
    const { store } = setup();
    const d = store.get('builtin', 'word-documents')!;
    expect(d.instructions.length).toBeGreaterThan(100);
    expect(d.version).toBe(1);
    expect(d.dir).toBe(join(SKILLS, 'word-documents'));
    expect(store.get('builtin', 'not-shipped')).toBeUndefined();
    expect(() => store.save({ scope: 'builtin', name: 'word-documents', instructions: 'x' })).toThrow(/read-only/);
    expect(() => store.delete('builtin', 'word-documents')).toThrow(/read-only/);
    expect(() => store.restore('builtin', 'word-documents', 1)).toThrow(/read-only/);
    expect(store.listScope('builtin')).toHaveLength(12);
  });

  it('works unchanged without built-ins', () => {
    const store = new SkillStore(mkdtempSync(join(tmpdir(), 'desk-store-')));
    expect(store.resolve('pdf-toolkit')).toBeUndefined();
    expect(store.list(undefined, { builtins: true })).toEqual([]);
  });
});
