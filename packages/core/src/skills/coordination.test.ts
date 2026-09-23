import { mkdirSync, writeFileSync } from 'node:fs';
import { join } from 'node:path';
import { afterEach, describe, expect, it } from 'vitest';
import { call, text, tools, type FakeReply, type Script } from '@desk/fake-model';
import { deskSystemPrompt, threadSystemPrompt } from '../agent/prompts';
import { deskToolsFor, threadToolsFor } from '../runtime/toolsets';
import { getAgent, getDeskAgent, getProject } from '../state/queries';
import { createHarness, FAKE_MODEL, newRuntime, type Harness } from '../testing/harness';

let h: Harness;
afterEach(async () => h?.cleanup());

async function setup(script: Script | FakeReply[] = [text('ok')]) {
  h = await createHarness({ script });
  const rt = newRuntime(h);
  const projectId = rt.createProject({ name: 'P', goal: 'G', settings: { desk_model: FAKE_MODEL.id, thread_model: FAKE_MODEL.id } });
  rt.saveSkill({ scope: 'global', name: 'weekly-report', description: 'Builds the weekly report', instructions: 'WEEKLY STEPS: run scripts/collect.sh', files: [{ path: 'scripts/collect.sh', content: 'echo data' }] });
  rt.saveSkill({ scope: 'project', projectId, name: 'house-style', description: 'Our writing style', instructions: 'STYLE: short sentences.' });
  const desk = getDeskAgent(h.store.db, projectId)!;
  const prompt = (id: string) => {
    const agent = getAgent(h.store.db, id)!;
    const ctx = { db: h.store.db, agent, project: getProject(h.store.db, projectId)!, libraryDir: rt.libraryDir(projectId), skills: rt.skills };
    return agent.role === 'desk' ? deskSystemPrompt(ctx) : threadSystemPrompt(ctx);
  };
  return { rt, projectId, desk, prompt };
}

describe('skills in coordination', () => {
  it('Desk prompt lists skills and the skill workflow; Desk has authoring tools, threads do not', async () => {
    const { desk, prompt } = await setup();
    const p = prompt(desk.id);
    expect(p).toContain('## Available skills');
    expect(p).toContain('- house-style (project, v1) — Our writing style');
    expect(p).toContain('- weekly-report (global, v1) — Builds the weekly report');
    expect(p).toContain('skill_write from_dir');
    expect(p).not.toContain('## Active skills');
    const deskTools = deskToolsFor(desk).map((t) => t.name);
    expect(deskTools).toEqual(expect.arrayContaining(['skill_list', 'skill_activate', 'skill_run', 'skill_write', 'skill_delete']));
    const threadTools = threadToolsFor({ ...desk, role: 'thread', git_branch: null }).map((t) => t.name);
    expect(threadTools).toEqual(expect.arrayContaining(['skill_list', 'skill_read', 'skill_activate', 'skill_run']));
    expect(threadTools).not.toContain('skill_write');
  });

  it('threads spawned with skills start with their instructions in the prompt', async () => {
    const { rt, desk, prompt } = await setup([tools(call('wait_for_reply', {}))]);
    const id = await rt.spawnThread(desk.id, { title: 'Report', brief: 'Make the report', skills: ['weekly-report'] });
    expect(getAgent(h.store.db, id)!.active_skills).toEqual(['weekly-report']);
    const p = prompt(id);
    expect(p).toContain('## Active skills');
    expect(p).toContain('WEEKLY STEPS: run scripts/collect.sh');
    expect(p).toContain('scripts/collect.sh (');
    expect(p).toContain('- house-style (project, v1)');
    expect(p).not.toContain('- weekly-report (global');
    expect(p).toContain('skill-drafts/<name>/');
    await expect(rt.spawnThread(desk.id, { title: 'X', brief: 'Y', skills: ['missing'] })).rejects.toThrow(/Unknown skill: missing/);
    await rt.whenIdle();
    // message_thread can add more skills
    rt.activateSkills(id, ['house-style']);
    expect(prompt(id)).toContain('STYLE: short sentences.');
    await rt.shutdown();
  });

  it('caps very large active skills', async () => {
    const { rt, desk, prompt } = await setup([tools(call('wait_for_reply', {}))]);
    rt.saveSkill({ scope: 'global', name: 'huge', description: 'Huge', instructions: 'H'.repeat(20_000) });
    const id = await rt.spawnThread(desk.id, { title: 'T', brief: 'B', skills: ['huge'] });
    const p = prompt(id);
    expect(p).toContain('truncated — read the rest with skill_read');
    expect(p.length).toBeLessThan(30_000);
    await rt.whenIdle();
    await rt.shutdown();
  });

  it('completion notices list skill drafts for Desk', async () => {
    let draftDir = '';
    const { rt, desk } = await setup((req) => {
      if (req.model !== FAKE_MODEL.id) return text('?');
      const sys = String(req.messages[0]?.content);
      if (sys.startsWith('You are Desk')) return tools(call('wait_for_threads', {}));
      const n = h.fake.requests.filter((r) => !String(r.messages[0]?.content).startsWith('You are Desk')).length;
      return n === 1 ? tools(call('complete', { summary: 'Drafted', skill_drafts: ['skill-drafts/csv-clean'] })) : text('done');
    });
    const id = await rt.spawnThread(desk.id, { title: 'Draft', brief: 'Draft a skill' });
    const ws = getAgent(h.store.db, id)!.workspace_path!;
    draftDir = join(ws, 'skill-drafts', 'csv-clean');
    mkdirSync(draftDir, { recursive: true });
    writeFileSync(join(draftDir, 'SKILL.md'), '---\nname: csv-clean\ndescription: Clean CSVs\n---\nSteps\n');
    await rt.whenIdle();
    const notice = h.store
      .list({ agentId: desk.id, types: ['message.agent'] })
      .map((e) => (e.type === 'message.agent' ? e.payload.text : ''))
      .find((t) => t.includes('Skill drafts'));
    expect(notice).toContain(`Skill drafts to review and install (skill_write from_dir): ${draftDir}`);
    await rt.shutdown();
  });
});
