import { join } from 'node:path';
import { afterEach, beforeEach, describe, expect, it } from 'vitest';
import type { SkillEnvProvider } from '../catalog/runtimes';
import type { SkillRef } from '../catalog/service';
import { createHarness, newRuntime, seedThread, type Harness } from '../testing/harness';

const SKILLS = join(import.meta.dirname, '..', '..', '..', '..', 'catalog', 'skills');

/** A provider that records ensures and lets tests decide when a build finishes. */
function fakeRuntimes() {
  const states = new Map<string, { state: 'none' | 'preparing' | 'ready' | 'failed'; digest?: string }>();
  const ensured: string[] = [];
  let finish: (() => void) | null = null;
  const key = (r: SkillRef) => `${r.scope}/${r.name}`;
  const p: SkillEnvProvider & { ensured: string[]; finish(): void } = {
    ensured,
    finish: () => finish?.(),
    env: (ref, expect) => {
      const s = states.get(key(ref));
      const state = !s ? 'none' : expect && s.state === 'ready' && s.digest !== expect ? 'none' : s.state;
      return { state, reason: null, bins: state === 'ready' ? ['/rt/bin'] : [], vars: {}, note: null };
    },
    remove: (ref) => void states.delete(key(ref)),
    ensure: (ref, spec) => {
      if (p.env(ref, spec.digest).state !== 'none') return;
      ensured.push(key(ref));
      states.set(key(ref), { state: 'preparing', digest: spec.digest });
    },
    waitFor: (ref, ms) =>
      new Promise((resolve) => {
        const t = setTimeout(() => resolve(false), ms);
        finish = () => {
          clearTimeout(t);
          states.set(key(ref), { ...states.get(key(ref))!, state: 'ready' });
          resolve(true);
        };
      }),
  };
  return p;
}

describe('built-in skills in the runtime', () => {
  let h: Harness;
  beforeEach(async () => (h = await createHarness()));
  afterEach(async () => h.cleanup());

  it('lists built-ins with their switch, shadowing and runtime', () => {
    const rt = newRuntime(h, { builtins: { root: SKILLS } });
    const pdf = () => rt.listBuiltins().find((b) => b.name === 'pdf-toolkit')!;
    expect(rt.listBuiltins()).toHaveLength(12);
    expect(pdf()).toMatchObject({ enabled: true, broken: null, shadowed_by: null, runtime: { state: 'none' } });
    expect(pdf().description.length).toBeGreaterThan(20);
    rt.setBuiltinEnabled('pdf-toolkit', false);
    expect(pdf().enabled).toBe(false);
    expect(rt.skills.resolve('pdf-toolkit')).toBeUndefined();
    rt.setBuiltinEnabled('pdf-toolkit', true);
    rt.saveSkill({ scope: 'global', name: 'pdf-toolkit', description: 'mine', instructions: 'x' });
    expect(pdf().shadowed_by).toBe('global');
    expect(() => rt.setBuiltinEnabled('nope', false)).toThrow(/Unknown built-in/);
  });

  it('duplicates a built-in into an ordinary skill that shadows it', () => {
    const rt = newRuntime(h, { builtins: { root: SKILLS } });
    const r = rt.duplicateBuiltin('images', { scope: 'global' });
    expect(r.created).toBe(true);
    expect(rt.skills.resolve('images')?.scope).toBe('global');
    expect(rt.skillHistory('global', 'images')[0]?.origin).toMatch(/^builtin:images@[0-9a-f]{12}$/);
    expect(() => rt.duplicateBuiltin('images', { scope: 'global' })).toThrow(/already exists/);
    rt.deleteSkill('global', 'images');
    expect(rt.skills.resolve('images')?.scope).toBe('builtin');
  });

  it('starts the environment when an agent activates a built-in, and skill runs wait for it', async () => {
    const env = fakeRuntimes();
    const rt = newRuntime(h, { builtins: { root: SKILLS }, skillEnv: env, builtinRuntimeWaitMs: 2_000 });
    const { agentId } = await seedThread(h.store, h.dir);
    rt.activateSkills(agentId, ['pdf-toolkit']);
    expect(env.ensured).toEqual(['builtin/pdf-toolkit']);
    const waiting = rt.prepareSkillRuntime(agentId, { scope: 'builtin', name: 'pdf-toolkit' });
    setTimeout(() => env.finish(), 50);
    expect((await waiting).waitedMs).toBeGreaterThanOrEqual(40);
    expect(rt.skillEnv(agentId, { scope: 'builtin', name: 'pdf-toolkit' }).bins).toEqual(['/rt/bin']);
    expect(rt.skillEnv(agentId).bins).toEqual(['/rt/bin']);
  });

  it('gives up waiting at the limit and says the environment is still setting up', async () => {
    const env = fakeRuntimes();
    const rt = newRuntime(h, { builtins: { root: SKILLS }, skillEnv: env, builtinRuntimeWaitMs: 30 });
    const { agentId } = await seedThread(h.store, h.dir);
    await rt.prepareSkillRuntime(agentId, { scope: 'builtin', name: 'images' });
    expect(rt.skillEnv(agentId, { scope: 'builtin', name: 'images' }).blocked).toMatch(/still setting up its Python environment \(first use only\)/);
  });

  it("gives a copy of a built-in the built-in's environment", async () => {
    const env = fakeRuntimes();
    const rt = newRuntime(h, { builtins: { root: SKILLS }, skillEnv: env });
    const { agentId } = await seedThread(h.store, h.dir);
    rt.duplicateBuiltin('archives', { scope: 'global' });
    rt.activateSkills(agentId, ['archives']);
    expect(env.ensured).toEqual(['builtin/archives']);
  });

  it('removes unmodified catalog copies at start and keeps edited ones', () => {
    const rt = newRuntime(h, { builtins: { root: SKILLS } });
    rt.saveSkill({ scope: 'global', name: 'pdf-toolkit', fromDir: join(SKILLS, 'pdf-toolkit') }, { origin: 'catalog:pdf-toolkit@builtin-0123456789ab' });
    rt.saveSkill({ scope: 'global', name: 'file-inspector', fromDir: join(SKILLS, 'file-inspector') }, { origin: 'catalog:file-inspector@builtin-0123456789ab' });
    rt.saveSkill({ scope: 'global', name: 'file-inspector', instructions: 'My changes.' }, { origin: 'user' });
    expect(rt.adoptBuiltins()).toEqual(['global::pdf-toolkit']);
    expect(rt.skills.resolve('pdf-toolkit')?.scope).toBe('builtin');
    expect(rt.skills.resolve('file-inspector')?.scope).toBe('global');
    expect(rt.adoptBuiltins()).toEqual([]);
  });

  it('never lets agents activate a switched-off built-in', async () => {
    const rt = newRuntime(h, { builtins: { root: SKILLS } });
    const { agentId } = await seedThread(h.store, h.dir);
    rt.setBuiltinEnabled('audio-video', false);
    expect(() => rt.activateSkills(agentId, ['audio-video'])).toThrow(/Unknown skill/);
  });
});
