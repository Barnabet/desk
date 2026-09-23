import { mkdtemp, realpath, rm } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { describe, expect, it } from 'vitest';
import {
  createModelAdapter,
  EventStore,
  getAgent,
  getDeskAgent,
  listThreads,
  loadModelConfig,
  ModelRegistry,
  openDb,
  renderTranscript,
  Runtime,
} from './index';

describe('live: skills on claude-opus-5-5', () => {
  it('Desk builds a global skill with a tested script, then another project uses it through a thread', async () => {
    const dir = await realpath(await mkdtemp(join(tmpdir(), 'desk-live-skills-')));
    const { db, close } = openDb(':memory:');
    const store = new EventStore(db);
    const models = new ModelRegistry();
    const rt = new Runtime({ store, adapter: createModelAdapter(loadModelConfig(), models), models, dataDir: dir, retry: { maxAttempts: 3 } });
    try {
      const a = rt.createProject({ name: 'Automations', goal: 'Reusable automations for the user.' });
      rt.sendToDesk(
        a,
        'Create a global skill named "slugify" with a tested Python script that turns any text given on stdin into a URL slug (lowercase ASCII, words joined by single hyphens). I will reuse it in other projects.',
      );
      await rt.whenIdle();
      const deskA = getDeskAgent(db, a)!;
      const skills = rt.listSkills();
      if (process.env.DESK_LIVE_VERBOSE || !skills.some((s) => s.name === 'slugify')) console.log(renderTranscript(store.list({ agentId: deskA.id })));
      const slugify = rt.getSkill('slugify');
      expect(slugify.scope).toBe('global');
      expect(slugify.files.some((f) => f.path.startsWith('scripts/'))).toBe(true);

      const b = rt.createProject({ name: 'Blog', goal: 'Blog posts.' });
      rt.sendToDesk(b, 'Have a thread compute the URL slug for the title "Hello, Wörld: Desk Skills!" and tell me the slug.');
      await rt.whenIdle();
      const deskB = getDeskAgent(db, b)!;
      const threads = listThreads(db, b);
      const usedSkill = threads.some((t) => getAgent(db, t.id)!.active_skills.includes('slugify'));
      const ran = store.list({ projectId: b, types: ['tool.call'] }).some((e) => e.type === 'tool.call' && e.payload.name === 'skill_run');
      if (process.env.DESK_LIVE_VERBOSE || !usedSkill || !ran) console.log(renderTranscript(store.list({ agentId: deskB.id })));
      expect(usedSkill).toBe(true);
      expect(ran).toBe(true);
    } finally {
      close();
      await rm(dir, { recursive: true, force: true });
    }
  });
});
