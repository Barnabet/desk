import { mkdtemp, realpath, rm } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { afterEach, beforeEach, describe, expect, it } from 'vitest';
import { DEFAULT_POLICY } from '@desk/protocol';
import { evaluatePolicy } from '../policy/evaluate';
import { testToolContext } from '../testing/context';
import { bashBackgroundTool, bashKillTool, bashOutputTool, JobManager } from './jobs';
import type { ToolContext } from './types';

let ws: string;
let ctx: ToolContext;
let jobs: JobManager;
beforeEach(async () => {
  ws = await realpath(await mkdtemp(join(tmpdir(), 'desk-jobs-')));
  jobs = new JobManager();
  ctx = testToolContext(ws, { jobs });
});
afterEach(async () => {
  jobs.killAll('a');
  jobs.killAll('b');
  await rm(ws, { recursive: true, force: true });
});

const until = async (fn: () => boolean, ms = 5000) => {
  const start = Date.now();
  while (!fn()) {
    if (Date.now() - start > ms) throw new Error('timeout');
    await new Promise((r) => setTimeout(r, 20));
  }
};
const jobId = (s: unknown) => /job_\w+/.exec(String(s))![0];

describe('background jobs', () => {
  it('streams incremental output and reports exit', async () => {
    const id = jobId(await bashBackgroundTool.execute({ command: 'echo a; sleep 0.3; echo b' }, ctx));
    await until(() => jobs.peek('a', id).output.includes('a'));
    const first = await bashOutputTool.execute({ job_id: id }, ctx);
    expect(first).toMatch(/^\[running\]\na/);
    await until(() => jobs.peek('a', id).status === 'exited');
    const second = await bashOutputTool.execute({ job_id: id }, ctx);
    expect(second).toBe('[exited with code 0]\nb\n');
  });

  it('kills a running job', async () => {
    const id = jobId(await bashBackgroundTool.execute({ command: 'sleep 30' }, ctx));
    expect(await bashKillTool.execute({ job_id: id }, ctx)).toContain('Killed');
    expect(jobs.peek('a', id).status).toBe('killed');
  });

  it('killAll stops every job of an agent', async () => {
    const a = jobs.start('a', { command: 'sleep 30', cwd: ws, env: process.env, sandbox: ctx.sandbox });
    const b = jobs.start('a', { command: 'sleep 30', cwd: ws, env: process.env, sandbox: ctx.sandbox });
    jobs.killAll('a');
    expect([jobs.peek('a', a).status, jobs.peek('a', b).status]).toEqual(['killed', 'killed']);
  });

  it('scopes jobs to their agent', async () => {
    const id = jobs.start('a', { command: 'true', cwd: ws, env: process.env, sandbox: ctx.sandbox });
    await expect(bashOutputTool.execute({ job_id: id }, { ...ctx, agentId: 'b' })).rejects.toThrow(/Unknown job/);
  });

  it('is gated like bash', () => {
    expect(evaluatePolicy(bashBackgroundTool, { command: 'sudo rm x' }, DEFAULT_POLICY, { sandboxAvailable: true }).action).toBe('ask');
    expect(evaluatePolicy(bashBackgroundTool, { command: 'npm run dev' }, DEFAULT_POLICY, { sandboxAvailable: true }).action).toBe('auto');
  });
});
