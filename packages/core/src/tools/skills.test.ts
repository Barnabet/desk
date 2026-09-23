import { existsSync } from 'node:fs';
import { mkdir, readFile, rm, writeFile } from 'node:fs/promises';
import { homedir } from 'node:os';
import { join } from 'node:path';
import { afterEach, beforeAll, beforeEach, describe, expect, it } from 'vitest';
import { DEFAULT_POLICY } from '@desk/protocol';
import { evaluatePolicy } from '../policy/evaluate';
import type { Runtime } from '../runtime/runtime';
import { getAgent, getDeskAgent } from '../state/queries';
import { testToolContext } from '../testing/context';
import { createHarness, FAKE_MODEL, newRuntime, type Harness } from '../testing/harness';
import { executeToolCall } from './registry';
import { detectSandbox } from './sandbox';
import { skillActivateTool, skillDeleteTool, skillListTool, skillReadTool, skillRunTool, skillWriteTool } from './skills';
import type { ToolContext } from './types';

let h: Harness;
let rt: Runtime;
let projectId: string;
let deskCtx: ToolContext;
let threadCtx: ToolContext;
let threadId: string;
let sandbox = false;

beforeAll(async () => {
  sandbox = await detectSandbox();
});

beforeEach(async () => {
  h = await createHarness();
  rt = newRuntime(h);
  projectId = rt.createProject({ name: 'P', goal: 'G', settings: { desk_model: FAKE_MODEL.id, thread_model: FAKE_MODEL.id } });
  const desk = getDeskAgent(h.store.db, projectId)!;
  threadId = rt.createThread(projectId, { title: 'T', brief: 'B', workspacePath: join(h.dir, 'workspaces', 'thread-1') });
  const base = { projectId, services: rt.services };
  deskCtx = testToolContext(desk.workspace_path!, { ...base, agentId: desk.id });
  threadCtx = testToolContext(join(h.dir, 'workspaces', 'thread-1'), { ...base, agentId: threadId });
});
afterEach(async () => {
  await rt.shutdown();
  await h.cleanup();
});

const call = async (tool: { name: string }, input: unknown, ctx: ToolContext) =>
  executeToolCall([skillListTool, skillReadTool, skillActivateTool, skillRunTool, skillWriteTool, skillDeleteTool], { id: 'c', name: tool.name, arguments: JSON.stringify(input) }, ctx);

describe('skill tools', () => {
  it('writes, lists, reads and refines skills with events', async () => {
    const created = await call(
      skillWriteTool,
      {
        name: 'greet',
        scope: 'global',
        description: 'Greets people. Use when asked to greet.',
        instructions: 'Run scripts/greet.sh with the name.',
        files: [{ path: 'scripts/greet.sh', content: '#!/bin/sh\necho "hello $1"\n' }],
        change_note: 'First version',
      },
      deskCtx,
    );
    expect(created.status).toBe('ok');
    expect(created.content).toMatch(/Created global skill "greet" v1/);
    expect((await call(skillListTool, {}, threadCtx)).content).toBe('- greet (global, v1) — Greets people. Use when asked to greet.');
    expect((await call(skillListTool, { query: 'nothing-like-this' }, threadCtx)).content).toMatch(/No skills match/);
    const read = await call(skillReadTool, { name: 'greet' }, threadCtx);
    expect(read.content).toContain('scripts/greet.sh');
    expect(read.content).toContain('Run scripts/greet.sh');
    expect((await call(skillReadTool, { name: 'greet', path: 'scripts/greet.sh' }, threadCtx)).content).toContain('echo "hello $1"');

    const updated = await call(skillWriteTool, { name: 'greet', scope: 'global', instructions: 'Run greet.sh, then smile.', change_note: 'Smile' }, deskCtx);
    expect(updated.content).toMatch(/Updated global skill "greet" v2/);
    const saved = h.store.list({ types: ['skill.saved'] });
    expect(saved.map((e) => e.type === 'skill.saved' && [e.payload.version, e.payload.change_note, e.payload.origin, e.project_id])).toEqual([
      [1, 'First version', `agent:${deskCtx.agentId}`, projectId],
      [2, 'Smile', `agent:${deskCtx.agentId}`, projectId],
    ]);
  });

  it('activates skills for the caller and returns the instructions immediately', async () => {
    rt.saveSkill({ scope: 'project', projectId, name: 'lint', description: 'Lint things', instructions: 'Step one: lint.' });
    const r = await call(skillActivateTool, { name: 'lint' }, threadCtx);
    expect(r.content).toContain('Step one: lint.');
    expect(getAgent(h.store.db, threadId)!.active_skills).toEqual(['lint']);
    await call(skillActivateTool, { name: 'lint' }, threadCtx);
    expect(h.store.list({ agentId: threadId, types: ['agent.skills_changed'] })).toHaveLength(1);
    expect((await call(skillListTool, {}, threadCtx)).content).toContain('(project, v1, active)');
    expect((await call(skillActivateTool, { name: 'nope' }, threadCtx)).status).toBe('error');
  });

  it('runs scripts with args, stdin and SKILL_DIR in the workspace', async () => {
    rt.saveSkill({
      scope: 'global',
      name: 'tools',
      description: 'Helpers',
      instructions: 'x',
      files: [
        { path: 'scripts/echo.py', content: 'import os, sys\nprint("args", sys.argv[1:])\nprint("stdin", sys.stdin.read().strip())\nprint("dir", os.path.basename(os.environ["SKILL_DIR"]))\nopen("made.txt", "w").write("ok")\n' },
        { path: 'scripts/noext', content: 'echo nope' },
      ],
    });
    const r = await call(skillRunTool, { name: 'tools', script: 'echo.py', args: ["it's", 'two words'], stdin: 'piped' }, threadCtx);
    expect(r.content).toContain('[exit code 0]');
    expect(r.content).toContain(`args ["it's", 'two words']`);
    expect(r.content).toContain('stdin piped');
    expect(r.content).toContain('dir tools');
    expect(await readFile(join(threadCtx.workspace, 'made.txt'), 'utf8')).toBe('ok');
    expect((await call(skillRunTool, { name: 'tools', script: 'scripts/missing.py' }, threadCtx)).status).toBe('error');
    expect((await call(skillRunTool, { name: 'tools', script: '../../etc/passwd' }, threadCtx)).status).toBe('error');
  });

  it('runs skill scripts inside the sandbox', async (t) => {
    if (!sandbox) t.skip();
    const escapee = join(homedir(), 'desk-skill-escape-test');
    rt.saveSkill({ scope: 'global', name: 'escape', description: 'd', instructions: 'x', files: [{ path: 'scripts/run.sh', content: `echo x > ${escapee}\n` }] });
    const ctx = { ...threadCtx, sandbox: { enabled: true, writable: [threadCtx.workspace] } };
    const r = await call(skillRunTool, { name: 'escape', script: 'run.sh' }, ctx);
    expect(r.content).toMatch(/operation not permitted/i);
    expect(existsSync(escapee)).toBe(false);
    await rm(escapee, { force: true });
  });

  it('installs drafts only from workspaces of the project', async () => {
    const draft = join(threadCtx.workspace, 'skill-drafts', 'csv-tool');
    await mkdir(join(draft, 'scripts'), { recursive: true });
    await writeFile(join(draft, 'SKILL.md'), '---\nname: csv-tool\ndescription: CSV helper\n---\nUse scripts/csv.py\n');
    await writeFile(join(draft, 'scripts', 'csv.py'), 'print(1)\n');
    const ok = await call(skillWriteTool, { name: 'csv-tool', from_dir: draft, change_note: 'Installed draft' }, deskCtx);
    expect(ok.content).toMatch(/Created project skill "csv-tool" v1/);
    expect(rt.getSkill('csv-tool', { projectId }).files.map((f) => f.path)).toEqual(['SKILL.md', 'scripts/csv.py']);
    const outside = join(h.dir, 'elsewhere');
    await mkdir(outside, { recursive: true });
    await writeFile(join(outside, 'SKILL.md'), '---\nname: x\ndescription: d\n---\nx\n');
    expect((await call(skillWriteTool, { name: 'x', from_dir: outside, change_note: 'n' }, deskCtx)).content).toMatch(/not inside a workspace/);
  });

  it('gates skill_run like the shell and skill_delete behind approval', () => {
    const run = (sandboxAvailable: boolean, args: string[]) =>
      evaluatePolicy(skillRunTool, { name: 'x', script: 'a.sh', args, timeout_s: 1 }, DEFAULT_POLICY, { sandboxAvailable }).action;
    expect(run(true, ['ok'])).toBe('auto');
    expect(run(true, ['; sudo rm'])).toBe('ask');
    expect(run(false, ['ok'])).toBe('ask');
    expect(evaluatePolicy(skillDeleteTool, { name: 'x', scope: 'global' }, DEFAULT_POLICY, { sandboxAvailable: true }).action).toBe('ask');
  });

  it('delete keeps history and the runtime can restore', async () => {
    rt.saveSkill({ scope: 'global', name: 'tmp', description: 'd', instructions: 'x' });
    await call(skillDeleteTool, { name: 'tmp', scope: 'global' }, deskCtx);
    expect(rt.listSkills(projectId)).toEqual([]);
    rt.restoreSkill('global', 'tmp', 1);
    expect(rt.listSkills(projectId).map((s) => [s.name, s.version])).toEqual([['tmp', 2]]);
    expect(h.store.list({ types: ['skill.deleted', 'skill.saved'] }).map((e) => e.type)).toEqual(['skill.saved', 'skill.deleted', 'skill.saved']);
  });

  it('imports a skill directory and adds skill roots to read roots', async () => {
    const src = join(h.dir, 'claude-skills', 'pdf-fill');
    await mkdir(src, { recursive: true });
    await writeFile(join(src, 'SKILL.md'), '---\nname: pdf-fill\ndescription: Fill PDF forms\n---\nSteps.\n');
    rt.importSkill(src, { scope: 'global' });
    expect(rt.getSkill('pdf-fill').description).toBe('Fill PDF forms');
    expect(() => rt.importSkill(join(h.dir, 'nothing'), { scope: 'global' })).toThrow(/no SKILL.md/);
  });
});
