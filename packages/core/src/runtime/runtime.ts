import { existsSync, mkdirSync, statSync } from 'node:fs';
import { realpath } from 'node:fs/promises';
import { basename, join } from 'node:path';
import type { AgentMessageKind, ProjectSettingsPatch } from '@desk/protocol';
import { buildToolContext } from '../agent/context';
import { hasPendingInbox } from '../agent/inbox';
import { threadSystemPrompt } from '../agent/prompts';
import { runAgent } from '../agent/run';
import type { EventStore } from '../events/store';
import { newId } from '../ids';
import { DEFAULT_MODEL_ID, type ModelRegistry } from '../model/registry';
import type { RetryOptions } from '../model/retry';
import type { ModelAdapter } from '../model/types';
import { evaluatePolicy } from '../policy/evaluate';
import { getAgent, getApproval, getDeskAgent, getProject, getSource, pendingApprovalsFor, type AgentRow, type ProjectRow } from '../state/queries';
import { bashTool } from '../tools/bash';
import { fileTools } from '../tools/fs';
import { JobManager, jobTools } from '../tools/jobs';
import { prepareToolCall, runPreparedTool } from '../tools/registry';
import { runProcess } from '../tools/process';
import { detectSandbox } from '../tools/sandbox';
import { completeTool } from '../tools/thread';
import type { Tool, ToolResult } from '../tools/types';
import { webTools } from '../tools/web';
import { Scheduler } from './scheduler';

export const defaultThreadTools: Tool[] = [...fileTools, bashTool, ...jobTools, ...webTools, completeTool];

export type RuntimeOptions = {
  store: EventStore;
  adapter: ModelAdapter;
  models: ModelRegistry;
  /** Root for project libraries, Desk scratch dirs and thread workspaces. */
  dataDir: string;
  toolsFor?: (agent: AgentRow) => Tool[];
  systemPrompt?: (agent: AgentRow, project: ProjectRow) => string;
  maxSteps?: { desk: number; thread: number };
  /** Overrides the project setting `max_concurrent_threads` for every project (tests). */
  maxConcurrentThreads?: number;
  retry?: RetryOptions;
  /** Force sandbox availability; detected via `sandbox-exec` when omitted. */
  sandboxAvailable?: boolean;
};

const TERMINAL = new Set(['done', 'failed', 'cancelled']);

export class Runtime {
  readonly scheduler: Scheduler;
  readonly jobs = new JobManager();

  constructor(private readonly o: RuntimeOptions) {
    this.scheduler = new Scheduler({
      modelConcurrency: (model) => o.models.get(model).concurrency,
      projectConcurrency: (projectId) =>
        o.maxConcurrentThreads ?? getProject(o.store.db, projectId)?.settings.max_concurrent_threads ?? 4,
      run: (job, signal) => this.execute(job.agentId, signal),
      afterRun: (job) => this.afterRun(job.agentId),
    });
  }

  // ── projects & agents ────────────────────────────────────────────────

  /** Creates a project, its directories and its Desk agent. */
  createProject(input: { name: string; goal: string; instructions?: string; settings?: ProjectSettingsPatch }): string {
    const id = newId();
    const deskDir = join(this.projectDir(id), 'desk');
    mkdirSync(deskDir, { recursive: true });
    mkdirSync(this.libraryDir(id), { recursive: true });
    this.o.store.append({
      project_id: id,
      agent_id: null,
      type: 'project.created',
      payload: { name: input.name, goal: input.goal, instructions: input.instructions ?? '', ...(input.settings ? { settings: input.settings } : {}) },
    });
    const deskModel = getProject(this.o.store.db, id)!.settings.desk_model;
    this.o.models.get(deskModel);
    this.o.store.append({
      project_id: id,
      agent_id: newId(),
      type: 'agent.created',
      payload: { role: 'desk', model: deskModel, title: 'Desk', brief: null, workspace_path: deskDir, parent_id: null },
    });
    return id;
  }

  projectDir(projectId: string): string {
    return join(this.o.dataDir, 'projects', projectId);
  }

  libraryDir(projectId: string): string {
    return join(this.projectDir(projectId), 'library');
  }

  /** Sends a user message to the project's Desk. */
  sendToDesk(projectId: string, text: string): void {
    const desk = getDeskAgent(this.o.store.db, projectId);
    if (!desk) throw new Error(`Unknown project: ${projectId}`);
    this.sendMessage(desk.id, text);
  }

  async addSource(projectId: string, path: string, label?: string): Promise<string> {
    if (!getProject(this.o.store.db, projectId)) throw new Error(`Unknown project: ${projectId}`);
    if (!existsSync(path) || !statSync(path).isDirectory()) throw new Error(`Source path does not exist or is not a directory: ${path}`);
    const real = await realpath(path);
    const top = await runProcess({ command: 'git', args: ['rev-parse', '--show-toplevel'], cwd: real, timeoutMs: 10_000 }).catch(() => null);
    const isRepoRoot = top?.exitCode === 0 && (await realpath(top.output.trim()).catch(() => '')) === real;
    const id = newId();
    this.o.store.append({
      project_id: projectId,
      agent_id: null,
      type: 'source.added',
      payload: { source_id: id, path: real, kind: isRepoRoot ? 'git' : 'folder', label: label ?? basename(real) },
    });
    return id;
  }

  removeSource(projectId: string, sourceId: string): void {
    const source = getSource(this.o.store.db, sourceId);
    if (!source || source.project_id !== projectId) throw new Error(`Unknown source: ${sourceId}`);
    this.o.store.append({ project_id: projectId, agent_id: null, type: 'source.removed', payload: { source_id: sourceId } });
  }

  /** Delivers an agent-to-agent message (or a system notice attributed to `fromAgentId`) and wakes the recipient. */
  sendAgentMessage(fromAgentId: string, toAgentId: string, kind: AgentMessageKind, text: string): void {
    const from = this.requireAgent(fromAgentId);
    const to = this.requireAgent(toAgentId);
    const label = from.role === 'desk' ? 'Desk' : `thread "${from.title ?? 'untitled'}" (${from.id})`;
    this.o.store.append({
      project_id: to.project_id,
      agent_id: to.id,
      type: 'message.agent',
      payload: { from_agent_id: from.id, from_label: label, kind, text },
    });
    this.wake(this.requireAgent(toAgentId));
  }

  updateProject(projectId: string, patch: { name?: string; goal?: string; instructions?: string; settings?: ProjectSettingsPatch }): void {
    if (!getProject(this.o.store.db, projectId)) throw new Error(`Unknown project: ${projectId}`);
    this.o.store.append({ project_id: projectId, agent_id: null, type: 'project.updated', payload: patch });
  }

  createThread(projectId: string, input: { title: string; brief: string; workspacePath: string; model?: string }): string {
    const model = input.model ?? getProject(this.o.store.db, projectId)?.settings.thread_model ?? DEFAULT_MODEL_ID;
    this.o.models.get(model);
    mkdirSync(input.workspacePath, { recursive: true });
    const id = newId();
    this.o.store.append({
      project_id: projectId,
      agent_id: id,
      type: 'agent.created',
      payload: {
        role: 'thread',
        model,
        title: input.title,
        brief: input.brief,
        workspace_path: input.workspacePath,
        parent_id: getDeskAgent(this.o.store.db, projectId)?.id ?? null,
      },
    });
    return id;
  }

  // ── messaging & control ──────────────────────────────────────────────

  sendMessage(agentId: string, text: string): void {
    const agent = this.requireAgent(agentId);
    this.o.store.append({ project_id: agent.project_id, agent_id: agentId, type: 'message.user', payload: { text } });
    this.wake(agent);
  }

  stop(agentId: string): void {
    const agent = this.requireAgent(agentId);
    this.jobs.killAll(agentId);
    const state = this.scheduler.stop(agentId);
    for (const ap of pendingApprovalsFor(this.o.store.db, agentId)) {
      this.o.store.append([
        { project_id: agent.project_id, agent_id: agentId, type: 'approval.resolved', payload: { approval_id: ap.id, decision: 'denied', resolved_by: 'system', note: 'Agent stopped' } },
        {
          project_id: agent.project_id,
          agent_id: agentId,
          type: 'tool.result',
          payload: { run_id: ap.run_id, tool_call_id: ap.tool_call_id, name: ap.tool, status: 'denied', content: 'Denied: the agent was stopped' },
        },
      ]);
    }
    if (state !== 'running' && !TERMINAL.has(this.requireAgent(agentId).status)) {
      this.o.store.append({
        project_id: agent.project_id,
        agent_id: agentId,
        type: 'agent.status_changed',
        payload: { status: 'cancelled', reason: state === 'queued' ? 'Stopped before starting' : 'Stopped' },
      });
    }
  }

  /** Resolves a pending approval: runs (or denies) the held tool call, then resumes the agent. */
  async resolveApproval(approvalId: string, decision: 'approved' | 'denied', opts: { by?: 'user' | 'desk'; note?: string } = {}): Promise<void> {
    const { store } = this.o;
    const ap = getApproval(store.db, approvalId);
    if (!ap) throw new Error(`Unknown approval: ${approvalId}`);
    if (ap.status !== 'pending') throw new Error(`Approval ${approvalId} is already resolved (${ap.status})`);
    const by = opts.by ?? 'user';
    const base = { project_id: ap.project_id, agent_id: ap.agent_id };
    store.append({ ...base, type: 'approval.resolved', payload: { approval_id: ap.id, decision, resolved_by: by, ...(opts.note ? { note: opts.note } : {}) } });

    const agent = this.requireAgent(ap.agent_id);
    let result: ToolResult;
    if (decision === 'approved') {
      const prepared = prepareToolCall(this.toolsFor(agent), { id: ap.tool_call_id, name: ap.tool, arguments: ap.arguments });
      result = prepared.ok
        ? await runPreparedTool(prepared.tool, prepared.input, await this.toolContext(agent, ap.run_id, ap.tool_call_id, new AbortController().signal))
        : prepared.result;
    } else {
      result = { status: 'denied', content: `Denied by ${by}${opts.note ? `: ${opts.note}` : ''}` };
    }
    store.append({
      ...base,
      type: 'tool.result',
      payload: { run_id: ap.run_id, tool_call_id: ap.tool_call_id, name: ap.tool, status: result.status, content: result.content },
    });

    const current = this.requireAgent(ap.agent_id);
    if (current.status === 'waiting' && pendingApprovalsFor(store.db, ap.agent_id).length === 0) this.schedule(current);
  }

  whenIdle(): Promise<void> {
    return this.scheduler.whenIdle();
  }

  // ── internals ────────────────────────────────────────────────────────

  private requireAgent(agentId: string): AgentRow {
    const agent = getAgent(this.o.store.db, agentId);
    if (!agent) throw new Error(`Unknown agent: ${agentId}`);
    return agent;
  }

  private toolsFor(agent: AgentRow): Tool[] {
    return this.o.toolsFor?.(agent) ?? defaultThreadTools;
  }

  private sandboxAvailable(): Promise<boolean> {
    return this.o.sandboxAvailable !== undefined ? Promise.resolve(this.o.sandboxAvailable) : detectSandbox();
  }

  private async toolContext(agent: AgentRow, runId: string, toolCallId: string, signal: AbortSignal) {
    return buildToolContext(agent, runId, toolCallId, signal, { sandboxEnabled: await this.sandboxAvailable(), jobs: this.jobs });
  }

  /** Schedules the agent unless it is already active or blocked on an approval. */
  private wake(agent: AgentRow): void {
    if (this.scheduler.isActive(agent.id)) return;
    if (pendingApprovalsFor(this.o.store.db, agent.id).length) return;
    this.schedule(agent);
  }

  private schedule(agent: AgentRow): void {
    this.o.store.append({ project_id: agent.project_id, agent_id: agent.id, type: 'agent.status_changed', payload: { status: 'queued' } });
    this.scheduler.enqueue({ agentId: agent.id, projectId: agent.project_id, model: agent.model, role: agent.role });
  }

  private async execute(agentId: string, signal: AbortSignal): Promise<void> {
    const agent = this.requireAgent(agentId);
    const maxSteps = this.o.maxSteps ?? { desk: 60, thread: 200 };
    const sandboxAvailable = await this.sandboxAvailable();
    await runAgent(
      {
        store: this.o.store,
        adapter: this.o.adapter,
        tools: this.toolsFor(agent),
        systemPrompt: this.o.systemPrompt ?? threadSystemPrompt,
        maxSteps: agent.role === 'desk' ? maxSteps.desk : maxSteps.thread,
        ...(this.o.retry ? { retry: this.o.retry } : {}),
        gate: (tool, input, project) => evaluatePolicy(tool, input, project.settings.policy, { sandboxAvailable }),
        toolContext: (a, runId, toolCallId, sig) => buildToolContext(a, runId, toolCallId, sig, { sandboxEnabled: sandboxAvailable, jobs: this.jobs }),
      },
      agentId,
      signal,
    );
  }

  private afterRun(agentId: string): void {
    const agent = this.requireAgent(agentId);
    if (TERMINAL.has(agent.status)) this.jobs.killAll(agentId);
    if (agent.status === 'cancelled') return;
    if (hasPendingInbox(this.o.store, agentId)) this.wake(agent);
  }
}
