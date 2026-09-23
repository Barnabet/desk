import type { AgentRole } from '@desk/protocol';

export type Job = { agentId: string; projectId: string; model: string; role: AgentRole };

export type SchedulerOptions = {
  modelConcurrency: (model: string) => number;
  projectConcurrency: (projectId: string) => number;
  run: (job: Job, signal: AbortSignal) => Promise<void>;
  afterRun?: (job: Job) => void;
};

type Running = { job: Job; controller: AbortController };

export class Scheduler {
  private queue: Job[] = [];
  private readonly running = new Map<string, Running>();
  private idleWaiters: Array<() => void> = [];

  constructor(private readonly opts: SchedulerOptions) {}

  get runningCount(): number {
    return this.running.size;
  }

  get queuedCount(): number {
    return this.queue.length;
  }

  enqueue(job: Job): void {
    if (this.isActive(job.agentId)) return;
    this.queue.push(job);
    // Stable sort: desk before thread, FIFO within each role.
    this.queue.sort((a, b) => Number(b.role === 'desk') - Number(a.role === 'desk'));
    this.pump();
  }

  isActive(agentId: string): boolean {
    return this.running.has(agentId) || this.queue.some((j) => j.agentId === agentId);
  }

  stop(agentId: string): 'queued' | 'running' | 'none' {
    const index = this.queue.findIndex((j) => j.agentId === agentId);
    if (index >= 0) {
      this.queue.splice(index, 1);
      this.pump();
      return 'queued';
    }
    const running = this.running.get(agentId);
    if (running) {
      running.controller.abort();
      return 'running';
    }
    return 'none';
  }

  whenIdle(): Promise<void> {
    if (this.queue.length === 0 && this.running.size === 0) return Promise.resolve();
    return new Promise((resolve) => this.idleWaiters.push(resolve));
  }

  private canStart(job: Job): boolean {
    const active = [...this.running.values()].map((r) => r.job);
    if (active.filter((j) => j.model === job.model).length >= this.opts.modelConcurrency(job.model)) return false;
    if (job.role === 'thread') {
      const threads = active.filter((j) => j.role === 'thread' && j.projectId === job.projectId).length;
      if (threads >= this.opts.projectConcurrency(job.projectId)) return false;
    }
    return true;
  }

  private start(job: Job): void {
    const controller = new AbortController();
    this.running.set(job.agentId, { job, controller });
    Promise.resolve()
      .then(() => this.opts.run(job, controller.signal))
      .catch(() => {})
      .finally(() => {
        this.running.delete(job.agentId);
        this.opts.afterRun?.(job);
        this.pump();
      });
  }

  private pump(): void {
    for (let i = 0; i < this.queue.length; ) {
      const job = this.queue[i]!;
      if (this.canStart(job)) {
        this.queue.splice(i, 1);
        this.start(job);
      } else {
        i++;
      }
    }
    if (this.queue.length === 0 && this.running.size === 0) {
      const waiters = this.idleWaiters;
      this.idleWaiters = [];
      for (const w of waiters) w();
    }
  }
}
