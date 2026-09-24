import { execFileSync } from 'node:child_process';
import { writeFileSync } from 'node:fs';
import { mkdtemp, realpath, rm } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { describe, expect, it } from 'vitest';
import { CatalogService, createModelAdapter, detectSandbox, EventStore, getDeskAgent, listThreads, loadModelConfig, ModelRegistry, openDb, renderTranscript, runProcess, Runtime, scrubbedEnv, shellInvocation, SkillRuntimes, withSkillEnv } from '../index';
import { shellWord } from './curation';

const uv = process.env.DESK_UV ?? execFileSync('/usr/bin/which', ['uv'], { encoding: 'utf8' }).trim();

describe('live: the skill catalog', () => {
  it('installs paper-lookup (Python) and pretty-mermaid (Node) from the real catalog, runs their smoke commands sandboxed, and a thread uses one', async () => {
    const dir = await realpath(await mkdtemp(join(tmpdir(), 'desk-live-catalog-')));
    const { db, close } = openDb(':memory:');
    const store = new EventStore(db);
    const models = new ModelRegistry();
    const sandbox = await detectSandbox();
    let rt: Runtime | null = null;
    const runtimes = new SkillRuntimes({ dataDir: dir, store, uv, nodeExec: process.execPath, exists: (r) => !!rt?.skills.get(r.scope, r.name, r.projectId) });
    rt = new Runtime({ store, adapter: createModelAdapter(loadModelConfig(), models), models, dataDir: dir, skillEnv: runtimes, sandboxAvailable: sandbox, retry: { maxAttempts: 3 } });
    const catalog = new CatalogService({ runtime: rt, store, dataDir: dir, runtimes, builtinRoot: join(dir, 'none') });
    try {
      for (const id of ['paper-lookup', 'pretty-mermaid']) {
        const review = await catalog.prepare(id);
        expect(review.license_text).toBeTruthy();
        await catalog.install(id);
        const ref = { scope: 'global' as const, name: id };
        await runtimes.settled(ref);
        expect(runtimes.state(ref)).toEqual({ state: 'ready', reason: null });
        const skill = rt.skills.get('global', id)!;
        const ws = await mkdtemp(join(dir, 'ws-'));
        const r = await runProcess({
          ...shellInvocation(`cd ${shellWord(skill.dir)} && ${review.entry.smoke!.map(shellWord).join(' ')}`, { enabled: sandbox, writable: [ws] }),
          cwd: ws,
          env: { ...withSkillEnv(scrubbedEnv(ws), runtimes.env(ref)), SKILL_DIR: skill.dir },
          timeoutMs: 180_000,
        });
        expect(r.exitCode, r.output).toBe(0);
      }

      const p = rt.createProject({ name: 'Docs', goal: 'Architecture docs.' });
      rt.sendToDesk(p, 'Have a thread use the pretty-mermaid skill to render this diagram as ASCII art, and show me the result: graph LR; Client-->Desk; Desk-->Threads');
      await rt.whenIdle();
      const ran = store
        .list({ projectId: p, types: ['tool.call'] })
        .some((e) => e.type === 'tool.call' && (e.payload.name === 'skill_run' || e.payload.name === 'bash') && JSON.stringify(e.payload).includes('pretty-mermaid'));
      const threads = listThreads(db, p);
      const transcript = [getDeskAgent(db, p)!, ...threads].map((t) => renderTranscript(store.list({ agentId: t.id }))).join('\n\n');
      if (process.env.DESK_LIVE_DUMP) writeFileSync(process.env.DESK_LIVE_DUMP, transcript);
      if (process.env.DESK_LIVE_VERBOSE || !ran) console.log(transcript);
      expect(ran).toBe(true);
    } finally {
      await rt.shutdown();
      close();
      await rm(dir, { recursive: true, force: true });
    }
  });
});
