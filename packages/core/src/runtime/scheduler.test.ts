import { describe, expect, it } from 'vitest';
import { Scheduler, type Job } from './scheduler';

type Gate = { job: Job; release: () => void; signal: AbortSignal };

function setup(opts: { model?: number; project?: number } = {}) {
  const started: Gate[] = [];
  const after: string[] = [];
  const s = new Scheduler({
    modelConcurrency: () => opts.model ?? 10,
    projectConcurrency: () => opts.project ?? 10,
    run: (job, signal) =>
      new Promise<void>((resolve) => {
        started.push({ job, release: resolve, signal });
        signal.addEventListener('abort', () => resolve());
      }),
    afterRun: (job) => after.push(job.agentId),
  });
  return { s, started, after };
}
const job = (agentId: string, extra: Partial<Job> = {}): Job => ({ agentId, projectId: 'p', model: 'm', role: 'thread', ...extra });
const tick = () => new Promise((r) => setTimeout(r, 0));

describe('Scheduler', () => {
  it('respects per-model concurrency', async () => {
    const { s, started } = setup({ model: 1 });
    s.enqueue(job('a'));
    s.enqueue(job('b'));
    await tick();
    expect(started.map((g) => g.job.agentId)).toEqual(['a']);
    started[0]!.release();
    await tick();
    expect(started.map((g) => g.job.agentId)).toEqual(['a', 'b']);
  });

  it('respects per-project thread concurrency but lets desk jobs through', async () => {
    const { s, started } = setup({ project: 1 });
    s.enqueue(job('t1'));
    s.enqueue(job('t2'));
    s.enqueue(job('d', { role: 'desk' }));
    await tick();
    expect(started.map((g) => g.job.agentId).sort()).toEqual(['d', 't1']);
  });

  it('does not let a blocked job block others', async () => {
    const { s, started } = setup({ model: 1 });
    s.enqueue(job('a', { model: 'm1' }));
    s.enqueue(job('b', { model: 'm1' }));
    s.enqueue(job('c', { model: 'm2' }));
    await tick();
    expect(started.map((g) => g.job.agentId)).toEqual(['a', 'c']);
  });

  it('orders desk jobs before thread jobs', async () => {
    const { s, started } = setup({ model: 1 });
    s.enqueue(job('blocker'));
    s.enqueue(job('t'));
    s.enqueue(job('d', { role: 'desk' }));
    await tick();
    started[0]!.release();
    await tick();
    expect(started.map((g) => g.job.agentId)).toEqual(['blocker', 'd']);
  });

  it('dedupes active agents and calls afterRun', async () => {
    const { s, started, after } = setup();
    s.enqueue(job('a'));
    s.enqueue(job('a'));
    await tick();
    expect(started).toHaveLength(1);
    expect(s.isActive('a')).toBe(true);
    started[0]!.release();
    await s.whenIdle();
    expect(after).toEqual(['a']);
    expect(s.isActive('a')).toBe(false);
  });

  it('stops queued and running jobs', async () => {
    const { s, started } = setup({ model: 1 });
    s.enqueue(job('a'));
    s.enqueue(job('b'));
    await tick();
    expect(s.stop('b')).toBe('queued');
    expect(s.stop('a')).toBe('running');
    expect(started[0]!.signal.aborted).toBe(true);
    expect(s.stop('zzz')).toBe('none');
    await s.whenIdle();
    expect(started).toHaveLength(1);
  });

  it('whenIdle resolves immediately when nothing is active', async () => {
    const { s } = setup();
    await expect(s.whenIdle()).resolves.toBeUndefined();
  });
});
