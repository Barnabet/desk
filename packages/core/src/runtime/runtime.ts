import { mkdirSync } from 'node:fs';
import { runAgent } from '../agent/run';
import { hasPendingInbox } from '../agent/inbox';
import { threadSystemPrompt } from '../agent/prompts';
import type { EventStore } from '../events/store';
import { newId } from '../ids';
import { DEFAULT_MODEL_ID, type ModelRegistry } from '../model/registry';
import type { RetryOptions } from '../model/retry';
import type { ModelAdapter } from '../model/types';
import { getAgent, type AgentRow, type ProjectRow } from '../state/queries';
import { bashTool } from '../tools/bash';
import { fileTools } from '../tools/fs';
import { completeTool } from '../tools/thread';
import type { Tool } from '../tools/types';
import { Scheduler } from './scheduler';

export const defaultThreadTools: Tool[] = [...fileTools, bashTool, completeTool];

export type RuntimeOptions = {
  store: EventStore;
  adapter: ModelAdapter;
  models: ModelRegistry;
  toolsFor?: (agent: AgentRow) => Tool[];
  systemPrompt?: (agent: AgentRow, project: ProjectRow) => string;
  maxSteps?: { desk: number; thread: number };
  maxConcurrentThreads?: number;
  retry?: RetryOptions;
};

export class Runtime {
  readonly scheduler: Scheduler;

  constructor(private readonly o: RuntimeOptions) {
    this.scheduler = new Scheduler({
      modelConcurrency: (model) => o.models.get(model).concurrency,
      projectConcurrency: () => o.maxConcurrentThreads ?? 4,
      run: (job, signal) => this.execute(job.agentId, signal),
      afterRun: (job) => this.afterRun(job.agentId),
    });
  }

  createProject(input: { name: string; goal: string; instructions?: string }): string {
    const id = newId();
    this.o.store.append({
      project_id: id,
      agent_id: null,
      type: 'project.created',
      payload: { name: input.name, goal: input.goal, instructions: input.instructions ?? '' },
    });
    return id;
  }

  createThread(projectId: string, input: { title: string; brief: string; workspacePath: string; model?: string }): string {
    const model = input.model ?? DEFAULT_MODEL_ID;
    this.o.models.get(model);
    mkdirSync(input.workspacePath, { recursive: true });
    const id = newId();
    this.o.store.append({
      project_id: projectId,
      agent_id: id,
      type: 'agent.created',
      payload: { role: 'thread', model, title: input.title, brief: input.brief, workspace_path: input.workspacePath, parent_id: null },
    });
    return id;
  }

  sendMessage(agentId: string, text: string): void {
    const agent = this.requireAgent(agentId);
    this.o.store.append({ project_id: agent.project_id, agent_id: agentId, type: 'message.user', payload: { text } });
    if (!this.scheduler.isActive(agentId)) this.schedule(agent);
  }

  stop(agentId: string): void {
    const agent = this.requireAgent(agentId);
    if (this.scheduler.stop(agentId) === 'queued') {
      this.o.store.append({
        project_id: agent.project_id,
        agent_id: agentId,
        type: 'agent.status_changed',
        payload: { status: 'cancelled', reason: 'Stopped before starting' },
      });
    }
  }

  whenIdle(): Promise<void> {
    return this.scheduler.whenIdle();
  }

  private requireAgent(agentId: string): AgentRow {
    const agent = getAgent(this.o.store.db, agentId);
    if (!agent) throw new Error(`Unknown agent: ${agentId}`);
    return agent;
  }

  private schedule(agent: AgentRow): void {
    this.o.store.append({ project_id: agent.project_id, agent_id: agent.id, type: 'agent.status_changed', payload: { status: 'queued' } });
    this.scheduler.enqueue({ agentId: agent.id, projectId: agent.project_id, model: agent.model, role: agent.role });
  }

  private async execute(agentId: string, signal: AbortSignal): Promise<void> {
    const agent = this.requireAgent(agentId);
    const maxSteps = this.o.maxSteps ?? { desk: 60, thread: 200 };
    await runAgent(
      {
        store: this.o.store,
        adapter: this.o.adapter,
        tools: this.o.toolsFor?.(agent) ?? defaultThreadTools,
        systemPrompt: this.o.systemPrompt ?? threadSystemPrompt,
        maxSteps: agent.role === 'desk' ? maxSteps.desk : maxSteps.thread,
        ...(this.o.retry ? { retry: this.o.retry } : {}),
      },
      agentId,
      signal,
    );
  }

  private afterRun(agentId: string): void {
    const agent = this.requireAgent(agentId);
    if (agent.status !== 'cancelled' && hasPendingInbox(this.o.store, agentId)) this.schedule(agent);
  }
}
