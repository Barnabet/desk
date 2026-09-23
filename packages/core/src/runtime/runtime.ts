import { existsSync, mkdirSync, statSync, writeFileSync } from 'node:fs';
import { copyFile, realpath } from 'node:fs/promises';
import { basename, join } from 'node:path';
import type { AgentMessageKind, ArtifactKind, MemoryKind, ProjectSettingsPatch } from '@desk/protocol';
import { uniqueLibraryName } from '../library/library';
import { createWorkspace, removeWorkspace, threadBranchName } from '../workspaces/workspaces';
import { ConflictError, NotFoundError, ValidationError } from '../errors';
import { getMemory } from '../memory/memory';
import { buildToolContext } from '../agent/context';
import { hasPendingInbox } from '../agent/inbox';
import { deskSystemPrompt, threadSystemPrompt } from '../agent/prompts';
import { runAgent, SHUTDOWN_REASON } from '../agent/run';
import type { EventStore } from '../events/store';
import { newId } from '../ids';
import { DEFAULT_MODEL_ID, type ModelRegistry } from '../model/registry';
import type { RetryOptions } from '../model/retry';
import type { ModelAdapter } from '../model/types';
import { evaluatePolicy } from '../policy/evaluate';
import {
  getAgent,
  getApproval,
  getDeskAgent,
  getProject,
  getSource,
  lastEvent,
  listActiveThreads,
  listAgents,
  listLiveAgents,
  listSources,
  pendingApprovalsFor,
  type AgentRow,
  type ProjectRow,
} from '../state/queries';
import { JobManager } from '../tools/jobs';
import { prepareToolCall, runPreparedTool } from '../tools/registry';
import { runProcess } from '../tools/process';
import { detectSandbox } from '../tools/sandbox';
import type { RuntimeServices, Tool, ToolResult } from '../tools/types';
import { Scheduler } from './scheduler';
import { toolsForRole } from './toolsets';

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
  /** Called with internal errors that are not tied to a request (defaults to console.error). */
  onError?: (err: unknown, context: string) => void;
};

const TERMINAL = new Set(['done', 'failed', 'cancelled']);

export class Runtime {
  readonly scheduler: Scheduler;
  readonly jobs = new JobManager();
  readonly services: RuntimeServices;
  /** Threads stopped by their Desk: their cancellation is not reported back to it. */
  private readonly silentStops = new Set<string>();
  /** Last event id already reported as stalled, per thread. */
  private readonly stallNotified = new Map<string, number>();
  private shuttingDown = false;

  constructor(private readonly o: RuntimeOptions) {
    this.services = {
      store: o.store,
      writeMemory: (projectId, input, source) => this.writeMemory(projectId, input, source),
      libraryDir: (projectId) => this.libraryDir(projectId),
      publishToLibrary: (projectId, file, meta, origin) => this.publishToLibrary(projectId, file, meta, origin),
      sendAgentMessage: (from, to, kind, text) => this.sendAgentMessage(from, to, kind, text),
      spawnThread: (parentId, input) => this.spawnThread(parentId, input),
      stopAgent: (agentId, opts) => this.stopAgent(agentId, opts),
      resolveApproval: (id, decision, opts) => this.resolveApproval(id, decision, opts),
      updateSettings: (projectId, patch) => this.updateSettings(projectId, patch),
    };
    this.scheduler = new Scheduler({
      modelConcurrency: (model) => o.models.get(model).concurrency,
      projectConcurrency: (projectId) =>
        o.maxConcurrentThreads ?? getProject(o.store.db, projectId)?.settings.max_concurrent_threads ?? 4,
      run: (job, signal) => this.execute(job.agentId, signal),
      afterRun: (job) => this.afterRun(job.agentId),
      onError: (err, job) => (o.onError ?? ((e, c) => console.error(`[desk] ${c}:`, e)))(err, `agent ${job.agentId}`),
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
    if (!desk) throw new NotFoundError(`Unknown project: ${projectId}`);
    this.requireOpenProject(projectId);
    this.sendMessage(desk.id, text);
  }

  async addSource(projectId: string, path: string, label?: string): Promise<string> {
    this.requireOpenProject(projectId);
    if (!existsSync(path) || !statSync(path).isDirectory()) throw new ValidationError(`Source path does not exist or is not a directory: ${path}`);
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
    if (!source || source.project_id !== projectId) throw new NotFoundError(`Unknown source: ${sourceId}`);
    this.o.store.append({ project_id: projectId, agent_id: null, type: 'source.removed', payload: { source_id: sourceId } });
  }

  // ── library ──────────────────────────────────────────────────────────

  /** Copies a file into the project library and records it as an artifact. */
  async publishToLibrary(
    projectId: string,
    file: string,
    meta: { title: string; kind: ArtifactKind; description: string; name?: string },
    origin: string,
  ): Promise<{ id: string; path: string }> {
    const dir = this.libraryDir(projectId);
    mkdirSync(dir, { recursive: true });
    const name = uniqueLibraryName(dir, meta.name ?? basename(file));
    await copyFile(file, join(dir, name));
    return this.recordArtifact(projectId, name, meta, origin);
  }

  /** Adds user-provided content to the library. */
  addLibraryFile(
    projectId: string,
    input: { name: string; content: string | Buffer; title?: string; kind?: ArtifactKind; description?: string },
  ): { id: string; path: string } {
    const dir = this.libraryDir(projectId);
    mkdirSync(dir, { recursive: true });
    const name = uniqueLibraryName(dir, input.name);
    writeFileSync(join(dir, name), input.content);
    return this.recordArtifact(projectId, name, { title: input.title ?? name, kind: input.kind ?? 'file', description: input.description ?? '' }, 'user');
  }

  private recordArtifact(
    projectId: string,
    path: string,
    meta: { title: string; kind: ArtifactKind; description: string },
    origin: string,
  ): { id: string; path: string } {
    const id = newId();
    this.o.store.append({
      project_id: projectId,
      agent_id: null,
      type: 'artifact.published',
      payload: { artifact_id: id, path, title: meta.title, kind: meta.kind, origin, description: meta.description },
    });
    return { id, path };
  }

  // ── memory ───────────────────────────────────────────────────────────

  writeMemory(projectId: string, input: { kind: MemoryKind; content: string; supersedes?: string }, source = 'user'): string {
    if (input.supersedes) {
      const old = getMemory(this.o.store.db, input.supersedes);
      if (!old || old.project_id !== projectId || old.superseded_by) throw new ConflictError(`Memory ${input.supersedes} is not active in this project`);
    }
    const id = newId();
    this.o.store.append({
      project_id: projectId,
      agent_id: null,
      type: 'memory.written',
      payload: { memory_id: id, kind: input.kind, content: input.content, source, ...(input.supersedes ? { supersedes: input.supersedes } : {}) },
    });
    return id;
  }

  deleteMemory(projectId: string, memoryId: string): void {
    const m = getMemory(this.o.store.db, memoryId);
    if (!m || m.project_id !== projectId) throw new NotFoundError(`Unknown memory: ${memoryId}`);
    this.o.store.append({ project_id: projectId, agent_id: null, type: 'memory.deleted', payload: { memory_id: memoryId } });
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
    this.requireOpenProject(projectId);
    this.o.store.append({ project_id: projectId, agent_id: null, type: 'project.updated', payload: patch });
  }

  /** Stops every agent of the project and archives it (data is kept). */
  archiveProject(projectId: string): void {
    this.requireOpenProject(projectId);
    for (const agent of listAgents(this.o.store.db, projectId)) {
      if (!TERMINAL.has(agent.status) || this.scheduler.isActive(agent.id)) this.stopAgent(agent.id, { by: agent.id, reason: 'Project archived' });
    }
    this.o.store.append({ project_id: projectId, agent_id: null, type: 'project.archived', payload: {} });
  }

  /** Validates model ids, then applies a settings patch. */
  updateSettings(projectId: string, patch: ProjectSettingsPatch): void {
    for (const key of ['desk_model', 'thread_model', 'fallback_model'] as const) {
      const model = patch[key];
      if (model) this.o.models.get(model);
    }
    this.updateProject(projectId, { settings: patch });
  }

  /** Creates a thread for Desk: its own workspace (git worktree for a repo source, else a scratch dir), then starts it. */
  async spawnThread(parentId: string, input: { title: string; brief: string; gitSourceId?: string; model?: string }): Promise<string> {
    const parent = this.requireAgent(parentId);
    const project = getProject(this.o.store.db, parent.project_id)!;
    const model = input.model ?? project.settings.thread_model;
    this.o.models.get(model);
    let gitSource: { id: string; path: string } | undefined;
    if (input.gitSourceId) {
      const source = getSource(this.o.store.db, input.gitSourceId);
      if (!source || source.project_id !== project.id || source.kind !== 'git') throw new ValidationError(`Unknown git source: ${input.gitSourceId}`);
      gitSource = { id: source.id, path: source.path };
    }
    const id = newId();
    const workspacePath = join(this.o.dataDir, 'workspaces', id);
    const ws = await createWorkspace({
      path: workspacePath,
      ...(gitSource ? { git: { sourcePath: gitSource.path, branch: threadBranchName(input.title, id) } } : {}),
    });
    this.o.store.append({
      project_id: project.id,
      agent_id: id,
      type: 'agent.created',
      payload: {
        role: 'thread',
        model,
        title: input.title,
        brief: input.brief,
        workspace_path: workspacePath,
        parent_id: parent.id,
        git: ws.git && gitSource ? { source_id: gitSource.id, ...ws.git } : null,
      },
    });
    this.sendAgentMessage(parent.id, id, 'note', 'Begin your assignment.');
    return id;
  }

  /** Deletes a finished thread's workspace (keeping any git branch) and marks it archived. */
  async archiveThread(threadId: string): Promise<void> {
    const t = this.requireAgent(threadId);
    if (t.role !== 'thread') throw new ValidationError('Only threads can be archived');
    if (!TERMINAL.has(t.status)) throw new ConflictError(`Thread ${threadId} is ${t.status}; stop it before archiving`);
    if (t.workspace_path) {
      const source = t.git_source_id ? getSource(this.o.store.db, t.git_source_id) : undefined;
      await removeWorkspace({ path: t.workspace_path, gitSourcePath: source?.path ?? null });
    }
    this.o.store.append({ project_id: t.project_id, agent_id: t.id, type: 'agent.archived', payload: {} });
  }

  /** Low-level thread creation in an existing directory. `parentId` defaults to the project's Desk; `null` makes a standalone thread. */
  createThread(projectId: string, input: { title: string; brief: string; workspacePath: string; model?: string; parentId?: string | null }): string {
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
        parent_id: input.parentId !== undefined ? input.parentId : (getDeskAgent(this.o.store.db, projectId)?.id ?? null),
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

  /** User-initiated stop. */
  stop(agentId: string): void {
    this.stopAgent(agentId);
  }

  /** Stops an agent: kills its jobs, denies its pending approvals, aborts or dequeues its run. `by` = stopping agent (not reported back to it). */
  stopAgent(agentId: string, opts: { by?: string; reason?: string } = {}): void {
    const agent = this.requireAgent(agentId);
    if (opts.by) this.silentStops.add(agentId);
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
        payload: { status: 'cancelled', reason: opts.reason ?? (state === 'queued' ? 'Stopped before starting' : 'Stopped') },
      });
      this.notifyParent(this.requireAgent(agentId));
    }
  }

  /** Reports running/waiting threads with no activity for `thresholdMs` to their Desk, once per stall. Returns reported thread ids. */
  checkStalls(now = Date.now(), thresholdMs = 15 * 60_000): string[] {
    const reported: string[] = [];
    for (const t of listActiveThreads(this.o.store.db)) {
      if (!t.parent_id) continue;
      if (pendingApprovalsFor(this.o.store.db, t.id).length) continue;
      const last = lastEvent(this.o.store.db, t.id);
      if (!last || now - Date.parse(last.ts) < thresholdMs || this.stallNotified.get(t.id) === last.id) continue;
      this.stallNotified.set(t.id, last.id);
      const minutes = Math.round((now - Date.parse(last.ts)) / 60_000);
      this.sendAgentMessage(t.id, t.parent_id, 'stalled', `No activity for ${minutes} minutes (status: ${t.status}).`);
      reported.push(t.id);
    }
    return reported;
  }

  /** Resolves a pending approval: runs (or denies) the held tool call, then resumes the agent. */
  async resolveApproval(approvalId: string, decision: 'approved' | 'denied', opts: { by?: 'user' | 'desk'; note?: string } = {}): Promise<void> {
    const { store } = this.o;
    const ap = getApproval(store.db, approvalId);
    if (!ap) throw new NotFoundError(`Unknown approval: ${approvalId}`);
    if (ap.status !== 'pending') throw new ConflictError(`Approval ${approvalId} is already resolved (${ap.status})`);
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

  /** Stops scheduling, interrupts running agents resumably and kills background jobs. */
  async shutdown(): Promise<void> {
    this.shuttingDown = true;
    this.scheduler.stopAll(SHUTDOWN_REASON);
    this.jobs.killEverything();
    await this.scheduler.whenIdle();
  }

  /**
   * On startup: repairs runs cut off by a crash (unfinished tool calls become `interrupted`), then reschedules
   * agents left queued or with undelivered messages. Returns the rescheduled agent ids.
   */
  recover(): string[] {
    const repairedByProject = new Map<string, number>();
    for (const agent of listLiveAgents(this.o.store.db, ['running'])) {
      this.repairCrashedRun(agent);
      repairedByProject.set(agent.project_id, (repairedByProject.get(agent.project_id) ?? 0) + 1);
    }
    for (const [projectId, count] of repairedByProject) {
      this.o.store.append({
        project_id: projectId,
        agent_id: null,
        type: 'system.notice',
        payload: { level: 'warning', code: 'daemon_restart', message: `Recovered ${count} run(s) interrupted by an unclean daemon shutdown.` },
      });
    }

    const resumed: string[] = [];
    for (const agent of listLiveAgents(this.o.store.db)) {
      const pendingInbox = hasPendingInbox(this.o.store, agent.id);
      if (agent.status === 'queued' || (pendingInbox && agent.status !== 'cancelled')) {
        if (pendingApprovalsFor(this.o.store.db, agent.id).length) continue;
        this.scheduler.enqueue({ agentId: agent.id, projectId: agent.project_id, model: agent.model, role: agent.role });
        resumed.push(agent.id);
      }
    }
    return resumed;
  }

  /** Closes a run that has `run.started` but no `run.finished`: never re-executes calls whose outcome is unknown. */
  private repairCrashedRun(agent: AgentRow): void {
    const { store } = this.o;
    const started = lastEvent(store.db, agent.id, 'run.started');
    if (started?.type !== 'run.started') return;
    const runId = started.payload.run_id;
    const since = store.list({ agentId: agent.id, after: started.id, types: ['tool.call', 'tool.result', 'approval.requested'] });
    const settled = new Set(since.flatMap((e) => (e.type === 'tool.result' || e.type === 'approval.requested' ? [e.payload.tool_call_id] : [])));
    const base = { project_id: agent.project_id, agent_id: agent.id };
    const interrupted = since.flatMap((e) =>
      e.type === 'tool.call' && !settled.has(e.payload.tool_call_id)
        ? [
            {
              ...base,
              type: 'tool.result' as const,
              payload: {
                run_id: runId,
                tool_call_id: e.payload.tool_call_id,
                name: e.payload.name,
                status: 'interrupted' as const,
                content: 'The daemon restarted during execution; the state of any side effects is unknown — verify before retrying.',
              },
            },
          ]
        : [],
    );
    const waiting = pendingApprovalsFor(store.db, agent.id).length > 0;
    store.append([
      ...interrupted,
      { ...base, type: 'run.finished', payload: { run_id: runId, reason: 'error', detail: 'daemon_restart' } },
      { ...base, type: 'agent.status_changed', payload: { status: waiting ? 'waiting' : 'queued', reason: 'Recovered after daemon restart' } },
    ]);
  }

  // ── internals ────────────────────────────────────────────────────────

  private requireAgent(agentId: string): AgentRow {
    const agent = getAgent(this.o.store.db, agentId);
    if (!agent) throw new NotFoundError(`Unknown agent: ${agentId}`);
    return agent;
  }

  private requireOpenProject(projectId: string): ProjectRow {
    const project = getProject(this.o.store.db, projectId);
    if (!project) throw new NotFoundError(`Unknown project: ${projectId}`);
    if (project.archived_at) throw new ConflictError(`Project ${projectId} is archived`);
    return project;
  }

  private toolsFor(agent: AgentRow): Tool[] {
    return this.o.toolsFor?.(agent) ?? toolsForRole(agent);
  }

  /** Own workspace + every project source + the project library. */
  private readRoots(agent: AgentRow): string[] {
    const roots = listSources(this.o.store.db, agent.project_id).map((s) => s.path);
    return [...(agent.workspace_path ? [agent.workspace_path] : []), ...roots, this.libraryDir(agent.project_id)];
  }

  private systemPrompt(agent: AgentRow, project: ProjectRow): string {
    if (this.o.systemPrompt) return this.o.systemPrompt(agent, project);
    const ctx = { db: this.o.store.db, agent, project, libraryDir: this.libraryDir(project.id) };
    return agent.role === 'desk' ? deskSystemPrompt(ctx) : threadSystemPrompt(ctx);
  }

  private sandboxAvailable(): Promise<boolean> {
    return this.o.sandboxAvailable !== undefined ? Promise.resolve(this.o.sandboxAvailable) : detectSandbox();
  }

  private async toolContext(agent: AgentRow, runId: string, toolCallId: string, signal: AbortSignal) {
    return buildToolContext(agent, runId, toolCallId, signal, {
      sandboxEnabled: await this.sandboxAvailable(),
      jobs: this.jobs,
      services: this.services,
      readRoots: (a) => this.readRoots(a),
    });
  }

  /** Schedules the agent unless it is already active or blocked on an approval. */
  private wake(agent: AgentRow): void {
    if (this.shuttingDown || this.scheduler.isActive(agent.id)) return;
    if (pendingApprovalsFor(this.o.store.db, agent.id).length) return;
    this.schedule(agent);
  }

  private schedule(agent: AgentRow): void {
    this.o.store.append({ project_id: agent.project_id, agent_id: agent.id, type: 'agent.status_changed', payload: { status: 'queued' } });
    // While shutting down, `queued` is recorded so the next daemon's recover() runs it.
    if (!this.shuttingDown) this.scheduler.enqueue({ agentId: agent.id, projectId: agent.project_id, model: agent.model, role: agent.role });
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
        systemPrompt: (a, p) => this.systemPrompt(a, p),
        maxSteps: agent.role === 'desk' ? maxSteps.desk : maxSteps.thread,
        ...(this.o.retry ? { retry: this.o.retry } : {}),
        gate: (tool, input, project, a) =>
          evaluatePolicy(tool, input, project.settings.policy, { sandboxAvailable, ...(a.git_branch ? { gitBranch: a.git_branch } : {}) }),
        toolContext: (a, runId, toolCallId, sig) => buildToolContext(a, runId, toolCallId, sig, {
            sandboxEnabled: sandboxAvailable,
            jobs: this.jobs,
            services: this.services,
            readRoots: (x) => this.readRoots(x),
          }),
      },
      agentId,
      signal,
    );
  }

  private afterRun(agentId: string): void {
    const agent = this.requireAgent(agentId);
    if (TERMINAL.has(agent.status)) this.jobs.killAll(agentId);
    this.notifyParent(agent);
    if (agent.status === 'cancelled') return;
    if (hasPendingInbox(this.o.store, agentId)) this.wake(this.requireAgent(agentId));
  }

  /** Tells a thread's Desk about the outcome of its latest run. */
  private notifyParent(agent: AgentRow): void {
    if (agent.role !== 'thread' || !agent.parent_id) return;
    const send = (kind: AgentMessageKind, text: string) => this.sendAgentMessage(agent.id, agent.parent_id!, kind, text);
    const finished = lastEvent(this.o.store.db, agent.id, 'run.finished');
    const fin = finished?.type === 'run.finished' ? finished.payload : undefined;
    switch (agent.status) {
      case 'done': {
        const artifacts = agent.result_artifacts ?? [];
        send('completed', `Summary: ${agent.result_summary ?? '(none)'}${artifacts.length ? `\nArtifacts: ${artifacts.join(', ')}` : ''}`);
        return;
      }
      case 'failed':
        send('failed', `Failed: ${fin?.detail ?? 'unknown error'}`);
        return;
      case 'cancelled':
        if (this.silentStops.delete(agent.id)) return;
        send('cancelled', 'Stopped by the user.');
        return;
      case 'waiting': {
        const pending = pendingApprovalsFor(this.o.store.db, agent.id).filter((ap) => ap.run_id === fin?.run_id);
        if (!pending.length) return;
        send(
          'approval',
          pending
            .map(
              (ap) =>
                `Approval ${ap.id} needed for ${ap.tool}(${ap.arguments}): ${ap.reason}. ${
                  ap.delegate_to_desk ? 'You may resolve it with resolve_approval.' : 'Waiting for the user to decide.'
                }`,
            )
            .join('\n'),
        );
        return;
      }
      case 'idle': {
        if (fin?.reason === 'max_steps') {
          send('update', 'Reached the step limit without completing.');
        } else if (fin?.reason === 'no_tool_calls') {
          const msg = lastEvent(this.o.store.db, agent.id, 'assistant.message');
          const content = msg?.type === 'assistant.message' ? msg.payload.content : null;
          send('update', `Ended its turn without completing: ${content ?? '(no text)'}`);
        }
        return;
      }
      default:
        return;
    }
  }
}
