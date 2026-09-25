import type { AgentRole } from '@desk/protocol';

/**
 * One agent's job: a full run, or an answer run (`answering`: the question it answers), which never changes the
 * agent's status (design spec §4).
 */
export type Job = { agentId: string; projectId: string; model: string; role: AgentRole; kind: 'run' | 'answer'; answering?: number };

export type SchedulerOptions = {
  modelConcurrency: (model: string) => number;
  projectConcurrency: (projectId: string) => number;
  run: (job: Job, signal: AbortSignal) => Promise<void>;
  afterRun?: (job: Job) => void;
  /** Receives errors thrown by `run` or `afterRun` (they never escape the scheduler). */
  onError?: (err: unknown, job: Job) => void;
};

/** A running job. `done` settles once its afterRun has returned. */
type Running = { job: Job; controller: AbortController; done: Promise<void> };

/** Queue order: Desk first, then answer jobs (short and read-only), then thread runs. */
const rank = (job: Job) => (job.role === 'desk' ? 0 : job.kind === 'answer' ? 1 : 2);

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
    // Stable sort: Desk, then answer jobs, then thread runs, FIFO within each.
    this.queue.sort((a, b) => rank(a) - rank(b));
    this.pump();
  }

  isActive(agentId: string): boolean {
    return this.jobOf(agentId) !== undefined;
  }

  /** The agent's running or queued job, if it has one. */
  jobOf(agentId: string): Job | undefined {
    return this.running.get(agentId)?.job ?? this.queue.find((j) => j.agentId === agentId);
  }

  /** Dequeues the agent's job, or aborts it if it is running. Returns where the job was, and the job. */
  stop(agentId: string): { state: 'queued' | 'running' | 'none'; job?: Job } {
    const index = this.queue.findIndex((j) => j.agentId === agentId);
    if (index >= 0) {
      const [job] = this.queue.splice(index, 1);
      this.pump();
      return { state: 'queued', job: job! };
    }
    const running = this.running.get(agentId);
    if (running) {
      running.controller.abort();
      return { state: 'running', job: running.job };
    }
    return { state: 'none' };
  }

  /** Stops like stop(), then resolves once a running job's afterRun has returned (at once when nothing was running). */
  stopAndWait(agentId: string): Promise<void> {
    const running = this.running.get(agentId);
    this.stop(agentId);
    return running ? running.done : Promise.resolve();
  }

  /** Drops every queued job and aborts every running one with `reason`. Returns the dropped queued jobs. */
  stopAll(reason: unknown): Job[] {
    const dropped = this.queue;
    this.queue = [];
    for (const r of this.running.values()) r.controller.abort(reason);
    this.pump();
    return dropped;
  }

  whenIdle(): Promise<void> {
    if (this.queue.length === 0 && this.running.size === 0) return Promise.resolve();
    return new Promise((resolve) => this.idleWaiters.push(resolve));
  }

  private canStart(job: Job): boolean {
    const active = [...this.running.values()].map((r) => r.job);
    if (active.filter((j) => j.model === job.model).length >= this.opts.modelConcurrency(job.model)) return false;
    // Answer jobs neither count toward the project's thread cap nor wait for it (design spec §3.4).
    if (job.role === 'thread' && job.kind === 'run') {
      const threads = active.filter((j) => j.role === 'thread' && j.kind === 'run' && j.projectId === job.projectId).length;
      if (threads >= this.opts.projectConcurrency(job.projectId)) return false;
    }
    return true;
  }

  private start(job: Job): void {
    const controller = new AbortController();
    let settle!: () => void;
    const done = new Promise<void>((resolve) => (settle = resolve));
    this.running.set(job.agentId, { job, controller, done });
    Promise.resolve()
      .then(() => this.opts.run(job, controller.signal))
      .catch((err: unknown) => this.opts.onError?.(err, job))
      .finally(() => {
        this.running.delete(job.agentId);
        try {
          this.opts.afterRun?.(job);
        } catch (err) {
          this.opts.onError?.(err, job);
        }
        this.pump();
        settle();
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
