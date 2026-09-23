import { spawn, type ChildProcess } from 'node:child_process';
import { z } from 'zod';
import { newId } from '../ids';
import { scrubbedEnv } from './bash';
import { shellInvocation, type SandboxSpec } from './sandbox';
import { defineTool } from './types';

const MAX_BUFFER = 1_000_000;

export type JobStatus = 'running' | 'exited' | 'killed';
export type JobSnapshot = { status: JobStatus; exitCode: number | null; output: string };

type Job = {
  agentId: string;
  child: ChildProcess;
  output: string;
  readOffset: number;
  status: JobStatus;
  exitCode: number | null;
};

/** Long-running shell processes owned by agents. Output is buffered (tail-capped) and read incrementally. */
export class JobManager {
  private readonly jobs = new Map<string, Job>();

  start(agentId: string, opts: { command: string; cwd: string; env: NodeJS.ProcessEnv; sandbox: SandboxSpec }): string {
    const inv = shellInvocation(opts.command, opts.sandbox);
    const child = spawn(inv.command, inv.args, { cwd: opts.cwd, env: opts.env, detached: true, stdio: ['ignore', 'pipe', 'pipe'] });
    const id = `job_${newId()}`;
    const job: Job = { agentId, child, output: '', readOffset: 0, status: 'running', exitCode: null };
    const onData = (buf: Buffer) => {
      job.output += buf.toString('utf8');
      if (job.output.length > MAX_BUFFER) {
        const cut = job.output.length - MAX_BUFFER;
        job.output = job.output.slice(cut);
        job.readOffset = Math.max(0, job.readOffset - cut);
      }
    };
    child.stdout?.on('data', onData);
    child.stderr?.on('data', onData);
    child.on('error', (err) => {
      job.output += `\n[failed to start: ${err.message}]\n`;
      if (job.status === 'running') job.status = 'exited';
    });
    child.on('close', (code) => {
      job.exitCode = code;
      if (job.status === 'running') job.status = 'exited';
    });
    this.jobs.set(id, job);
    return id;
  }

  private get(agentId: string, jobId: string): Job {
    const job = this.jobs.get(jobId);
    if (!job || job.agentId !== agentId) throw new Error(`Unknown job: ${jobId}`);
    return job;
  }

  /** Returns output produced since the previous read. */
  read(agentId: string, jobId: string): JobSnapshot {
    const job = this.get(agentId, jobId);
    const output = job.output.slice(job.readOffset);
    job.readOffset = job.output.length;
    return { status: job.status, exitCode: job.exitCode, output };
  }

  /** Like read, without consuming output. */
  peek(agentId: string, jobId: string): JobSnapshot {
    const job = this.get(agentId, jobId);
    return { status: job.status, exitCode: job.exitCode, output: job.output.slice(job.readOffset) };
  }

  kill(agentId: string, jobId: string): boolean {
    const job = this.get(agentId, jobId);
    if (job.status !== 'running') return false;
    job.status = 'killed';
    const pid = job.child.pid;
    if (pid !== undefined) {
      try {
        process.kill(-pid, 'SIGTERM');
      } catch {}
      setTimeout(() => {
        try {
          process.kill(-pid, 'SIGKILL');
        } catch {}
      }, 5000).unref();
    }
    return true;
  }

  killAll(agentId: string): void {
    for (const [id, job] of this.jobs) if (job.agentId === agentId) this.kill(agentId, id);
  }

  killEverything(): void {
    for (const [id, job] of this.jobs) this.kill(job.agentId, id);
  }
}

export const bashBackgroundTool = defineTool({
  name: 'bash_background',
  description:
    'Start a long-running shell command (dev server, watcher, long build) in your workspace without waiting. Returns a job id; read output with bash_output, stop with bash_kill.',
  input: z.object({ command: z.string().min(1) }),
  gate: { subject: (i) => ({ command: i.command }), unmatched: 'auto' },
  async execute({ command }, ctx) {
    const id = ctx.jobs.start(ctx.agentId, { command, cwd: ctx.workspace, env: scrubbedEnv(ctx.workspace), sandbox: ctx.sandbox });
    return `Started ${id}. Use bash_output to read its output and bash_kill to stop it.`;
  },
});

export const bashOutputTool = defineTool({
  name: 'bash_output',
  description: 'Read new output from a background job started with bash_background.',
  input: z.object({ job_id: z.string() }),
  async execute({ job_id }, ctx) {
    const s = ctx.jobs.read(ctx.agentId, job_id);
    const status = s.status === 'running' ? 'running' : s.status === 'killed' ? 'killed' : `exited with code ${s.exitCode}`;
    return `[${status}]\n${s.output}`;
  },
});

export const bashKillTool = defineTool({
  name: 'bash_kill',
  description: 'Stop a background job.',
  input: z.object({ job_id: z.string() }),
  async execute({ job_id }, ctx) {
    return ctx.jobs.kill(ctx.agentId, job_id) ? `Killed ${job_id}` : `${job_id} was not running`;
  },
});

export const jobTools = [bashBackgroundTool, bashOutputTool, bashKillTool];
