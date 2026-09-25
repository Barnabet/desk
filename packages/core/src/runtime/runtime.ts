import { existsSync, mkdirSync, realpathSync, statSync, writeFileSync } from 'node:fs';
import { realpath } from 'node:fs/promises';
import { homedir } from 'node:os';
import { basename, dirname, join, sep } from 'node:path';
import {
  emptyMessages,
  foldMessages,
  GLOBAL_PROJECT_ID,
  MESSAGE_FOLD_TYPES,
  messageById,
  openFrom,
  openTo,
  sanitizeLabel,
  sentSince,
  ServiceName,
  snippet,
  WAKES_PAUSED,
  type AgentMessageKind,
  type ArtifactKind,
  type EventInput,
  type MemoryKind,
  type MessagesState,
  type MessageView,
  type ProjectSettingsPatch,
  type ReasoningEffort,
  type ServiceStopReason,
  type SkillScope,
  type StoredEvent,
} from '@desk/protocol';
import { SkillStore, type SkillDetail, type SkillSaveInput, type SkillSummary } from '../skills/store';
import { AttachmentStore } from '../attachments/store';
import { uniqueLibraryName } from '../library/library';
import { createWorkspace, gitEnv, removeWorkspace, safeGitArgs, threadBranchName } from '../workspaces/workspaces';
import { ConflictError, NotFoundError, ValidationError } from '../errors';
import { getMemory } from '../memory/memory';
import { buildToolContext } from '../agent/context';
import { pendingInbox } from '../agent/inbox';
import { deskSystemPrompt, threadSystemPrompt } from '../agent/prompts';
import { answerGate, answerText, ANSWER_MAX_STEPS, closureText, WHY } from '../agent/answer';
import { runAgent, SHUTDOWN_REASON, type RunDeps } from '../agent/run';
import type { EventStore } from '../events/store';
import { newId } from '../ids';
import { DEFAULT_MODEL_ID, effortFor, type ModelRegistry } from '../model/registry';
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
  listProjects,
  findService,
  getService,
  listRunningServices,
  listServices,
  listSources,
  pendingApprovalsFor,
  threadsByRef,
  type AgentRow,
  type ProjectRow,
  type ServiceRow,
} from '../state/queries';
import { reapOrphan, ServiceProcesses, tailLog } from '../services/manager';
import { resolveInside } from '../tools/paths';
import { scrubbedEnv, withSkillEnv } from '../tools/bash';
import { listAttention, pausedNotice } from '../state/attention';
import { JobManager } from '../tools/jobs';
import { prepareToolCall, runPreparedTool } from '../tools/registry';
import { runProcess } from '../tools/process';
import { readAgentFile } from '../tools/agent-files';
import { detectSandbox, isWithin, realOrSelf, sandboxGuard, type SandboxGuard } from '../tools/sandbox';
import type { RuntimeServices, SendInput, SendResult, Tool, ToolResult } from '../tools/types';
import type { SkillEnvProvider } from '../catalog/runtimes';
import { ProxyGate } from './proxy-gate';
import { REMINDER_LABEL, WHATS_UP_REMINDER, whatsUpStale } from '../coordination/whatsup';
import { Scheduler, type Job } from './scheduler';
import { toolsForRole } from './toolsets';
import { takeWake, wakeDecision, type Item, type Trigger, type WakeState } from './wake';

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
  /** Health-probe interval while the model proxy is down (default 10 s). */
  proxyProbeIntervalMs?: number;
  /** Called with internal errors that are not tied to a request (defaults to console.error). */
  onError?: (err: unknown, context: string) => void;
  /** Desk-managed runtimes of catalog skills (PATH for scripts, removal with the skill). */
  skillEnv?: SkillEnvProvider;
  /** Remind Desk to update What's up when it ends a turn after changing things (default on; the test harness turns it off). */
  whatsUpReminder?: boolean;
  /** Files agents may never read or replace: deskd's token file, its database, model credentials (see `SandboxGuard`). */
  secrets?: string[];
  /** Files and folders agents may never write, whatever their roots: the git and ssh config deskd's git runs with. */
  readOnly?: string[];
  /** The user's home folder (default `os.homedir()`), which can never be a source. */
  home?: string;
  /** Wakes of one agent by another a project may have in a rolling hour before its automatic wakes pause (default 60). */
  wakeBudget?: number;
  /** Lifecycle wakes (thread starts, notices to Desk) a project may have in a rolling hour before it pauses (default 150). */
  lifecycleBudget?: number;
};

/** What the send path records on a message besides its text (design spec §1.2). */
export type DeliverOptions = {
  /** The question this message answers. */
  replyTo?: number;
  /** An answer the runtime writes for a thread (a closure). */
  auto?: boolean;
  /** A question that gets a state (open, answered, closed, withdrawn). */
  tracked?: boolean;
  /** The tool call that sent it. */
  toolCallId?: string;
};

/** The longest message the send tools take (design spec §5.3). Only tool inputs are checked, so old events stay valid. */
export const MAX_MESSAGE_CHARS = 4000;
/** Questions and notes one thread may send other threads per rolling hour; answers are exempt (design spec §5.3). */
export const SENDER_CAP = 20;
const HOUR_MS = 60 * 60_000;
/** The outcome a send's result names when the pause holds the recipient (design spec §2.5, §5.4). */
const PAUSED_NOTE = 'automatic wakes are paused in this project; it reads this once the user resumes them';

/** How a refusal or a tool result names an agent: Desk, or a thread's quoted, sanitised title. */
function nameOf(a: AgentRow): string {
  return a.role === 'desk' ? 'Desk' : `"${sanitizeLabel(a.title ?? 'untitled')}"`;
}

const TERMINAL = new Set(['done', 'failed', 'cancelled']);
/** The result recovery records for a call cut off by an unclean exit: it is never run again. */
const INTERRUPTED_BY_RESTART = 'The daemon restarted during execution; the state of any side effects is unknown — verify before retrying.';

export class Runtime {
  readonly scheduler: Scheduler;
  readonly jobs = new JobManager();
  readonly services: RuntimeServices;
  /** Project services' processes (long-lived, outlive their thread's runs). */
  readonly serviceProcs: ServiceProcesses;
  readonly skills: SkillStore;
  /** Images shown to models (`<data>/attachments`), served by GET /v1/attachments/:sha256. */
  readonly attachments: AttachmentStore;
  /** What agents' commands and file tools may never touch; deskd adds its port with `guardPort`. */
  readonly guard: SandboxGuard;
  private readonly proxy: ProxyGate | undefined;
  /** Bytes of images per request, by model, lowered after an endpoint refused a request as too large (until restart). */
  private readonly imageBudgets = new Map<string, number>();
  /** Threads stopped by their Desk: their cancellation is not reported back to it. */
  private readonly silentStops = new Set<string>();
  /** Last event id already reported as stalled, per thread. */
  private readonly stallNotified = new Map<string, number>();
  /** Approved calls resolveApproval is still running, per agent: until their results exist, nothing may run the agent. */
  private readonly resolving = new Map<string, number>();
  /** Each project's message fold, brought up to date on every read (messages()). */
  private readonly folds = new Map<string, MessagesState>();
  /** Threads being archived: from archiveThread's first step, nothing new reaches them (design spec §3.5). */
  private readonly archiving = new Set<string>();
  /** Per project, the times of the counted wakes of the last hour, by trigger (design spec §5.4). Empty after a restart. */
  private readonly wakeWindows = new Map<string, { agent: number[]; lifecycle: number[] }>();
  /** Projects whose automatic wakes are paused until the user writes to one of their agents or resumes them (§5.4). */
  private readonly paused = new Set<string>();
  /** Those calls' abort controllers, and when each resolution has recorded its result: a shutdown aborts and awaits them. */
  private readonly resolutions = new Set<{ controller: AbortController; recorded: Promise<void> }>();
  /**
   * Running jobs stopped by stopAgent, until their afterRun: the agent's last event id at the stop. The run appends
   * `cancelled` only when it winds down, and a user message sent in between still counts as sent after the stop.
   */
  private readonly stoppedAt = new Map<string, number>();
  private shuttingDown = false;

  constructor(private readonly o: RuntimeOptions) {
    const health = o.adapter.health?.bind(o.adapter);
    this.proxy = health
      ? new ProxyGate(health, o.proxyProbeIntervalMs ?? 10_000, (projectId, up) =>
          o.store.append({
            project_id: projectId,
            agent_id: null,
            type: 'system.notice',
            payload: up
              ? { level: 'info', code: 'proxy_up', message: 'The model proxy is reachable again; paused agents resumed.' }
              : { level: 'error', code: 'proxy_down', message: 'The model proxy is unreachable; agents are paused until it comes back.' },
          }),
        )
      : undefined;
    this.guard = sandboxGuard({ dataDir: o.dataDir, secrets: o.secrets ?? [], readOnly: o.readOnly ?? [] });
    this.skills = new SkillStore(o.dataDir, this.guard);
    this.attachments = new AttachmentStore(join(o.dataDir, 'attachments'));
    this.services = {
      store: o.store,
      skills: this.skills,
      attachments: this.attachments,
      agentModel: (agentId, model) => {
        const id = model ?? this.requireAgent(agentId).model;
        return { id, vision: this.modelSeesImages(id) };
      },
      activateSkills: (agentId, names) => this.activateSkills(agentId, names),
      saveSkill: (input, meta) => this.saveSkill(input, meta),
      deleteSkill: (scope, name, projectId, meta) => this.deleteSkill(scope, name, projectId, meta),
      skillDraftDir: (projectId, path) => this.skillDraftDir(projectId, path),
      writeMemory: (projectId, input, source) => this.writeMemory(projectId, input, source),
      libraryDir: (projectId) => this.libraryDir(projectId),
      publishToLibrary: (projectId, file, meta, origin) => this.publishToLibrary(projectId, file, meta, origin),
      deliver: (from, to, kind, text, opts) => this.deliver(from, to, kind, text, opts),
      send: (input) => this.send(input),
      messages: (projectId) => this.messages(projectId),
      spawnThread: (parentId, input) => this.spawnThread(parentId, input),
      heldByPause: (agentId) => this.heldByPause(agentId),
      stopAgent: (agentId, opts) => this.stopAgent(agentId, opts),
      isStopping: (agentId) => this.stoppedAt.has(agentId),
      resolveApproval: (id, decision, opts) => this.resolveApproval(id, decision, opts),
      updateSettings: (projectId, patch) => this.updateSettings(projectId, patch),
      skillEnv: (agentId, only) => this.skillEnv(agentId, only),
      startService: (projectId, input) => this.startService(projectId, input),
      stopService: (serviceId, by) => this.stopService(serviceId, by),
      restartService: (serviceId, by) => this.restartService(serviceId, by),
      serviceLogs: (serviceId, lines) => this.serviceLogs(serviceId, lines),
    };
    this.serviceProcs = new ServiceProcesses({
      onUrl: (serviceId, url) => {
        const s = getService(o.store.db, serviceId);
        if (s?.status === 'running') o.store.append({ project_id: s.project_id, agent_id: null, type: 'service.url', payload: { service_id: serviceId, url } });
      },
      onExit: (serviceId, code, signal) => {
        const s = getService(o.store.db, serviceId);
        if (s?.status === 'running') o.store.append({ project_id: s.project_id, agent_id: null, type: 'service.exited', payload: { service_id: serviceId, code, signal } });
      },
    });
    this.scheduler = new Scheduler({
      modelConcurrency: (model) => o.models.get(model).concurrency,
      projectConcurrency: (projectId) =>
        o.maxConcurrentThreads ?? getProject(o.store.db, projectId)?.settings.max_concurrent_threads ?? 4,
      run: (job, signal) => this.execute(job, signal),
      afterRun: (job) => this.afterRun(job),
      onError: (err, job) => (o.onError ?? ((e, c) => console.error(`[desk] ${c}:`, e)))(err, `agent ${job.agentId}`),
    });
  }

  /** Model proxy reachability as last observed ('unknown' without a health probe). */
  get proxyState(): 'up' | 'down' | 'unknown' {
    return this.proxy ? (this.proxy.isDown ? 'down' : 'up') : 'unknown';
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

  /**
   * Dismisses a needs_you, stalled, failed or paused attention item. Dismissing `paused` is the Resume action: the
   * project's automatic wakes resume (design spec §5.4). Approvals and questions leave the list when answered.
   */
  dismissAttention(itemId: string): void {
    const kind = itemId.split(':')[0];
    if (kind === 'approval' || kind === 'question') throw new ConflictError(`${kind} items leave the list when they are answered`);
    const item = listAttention(this.o.store.db).find((i) => i.id === itemId);
    if (!item) throw new NotFoundError(`No attention item ${itemId}`);
    this.o.store.append({ project_id: item.project_id, agent_id: null, type: 'attention.dismissed', payload: { item_id: itemId } });
    if (item.kind === 'paused') this.resume(item.project_id);
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

  async addSource(projectId: string, path: string, label?: string, agentWrite = true): Promise<string> {
    this.requireOpenProject(projectId);
    if (!existsSync(path) || !statSync(path).isDirectory()) throw new ValidationError(`Source path does not exist or is not a directory: ${path}`);
    const real = await realpath(path);
    const unsafe = this.unsafeSource(real);
    if (unsafe) throw new ValidationError(`A project source cannot be ${unsafe}: ${path}`);
    const top = await runProcess({ command: 'git', args: safeGitArgs(['rev-parse', '--show-toplevel']), cwd: real, env: gitEnv(), timeoutMs: 10_000 }).catch(() => null);
    const isRepoRoot = top?.exitCode === 0 && (await realpath(top.output.trim()).catch(() => '')) === real;
    const id = newId();
    this.o.store.append({
      project_id: projectId,
      agent_id: null,
      type: 'source.added',
      payload: { source_id: id, path: real, kind: isRepoRoot ? 'git' : 'folder', label: label ?? basename(real), agent_write: agentWrite },
    });
    return id;
  }

  /** Lets agents write to a source folder (sandboxed) and run services there, or takes that back. */
  setSourceWrite(projectId: string, sourceId: string, agentWrite: boolean): void {
    const source = getSource(this.o.store.db, sourceId);
    if (!source || source.project_id !== projectId) throw new NotFoundError(`Unknown source: ${sourceId}`);
    if (source.agent_write === agentWrite) return;
    this.o.store.append({ project_id: projectId, agent_id: null, type: 'source.updated', payload: { source_id: sourceId, agent_write: agentWrite } });
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
    // `file` is an agent's: read it without following a swapped-in symlink, and never a secret.
    writeFileSync(join(dir, name), await readAgentFile(file, this.guard));
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

  /**
   * Stores a message on the recipient's stream (another agent's message, an answer, or a runtime notice attributed to
   * `fromAgentId`), then wakes the recipient if wakeDecision says so. It never refuses because of the recipient's
   * state. Returns the message id.
   */
  deliver(fromAgentId: string, toAgentId: string, kind: AgentMessageKind, text: string, opts: DeliverOptions = {}): number {
    const [message] = this.o.store.append(this.messageEvent(this.requireAgent(fromAgentId), this.requireAgent(toAgentId), kind, text, opts));
    this.wake(toAgentId);
    return message!.id;
  }

  /**
   * Writes the one answer to a question (design spec §1.3), from its recipient to its asker, then wakes the asker.
   * Returns null, appending nothing, when answerEvent's guard allows no answer.
   */
  answer(questionId: number, fromAgentId: string, text: string, opts: { auto?: boolean; toolCallId?: string } = {}): number | null {
    const event = this.answerEvent(questionId, fromAgentId, text, opts);
    if (!event) return null;
    const [message] = this.o.store.append(event);
    this.wake(message!.agent_id!);
    return message!.id;
  }

  /**
   * The project's messages, question states and answer runs (design spec §1.4). One fold per project is kept in
   * memory; each call folds only the events stored since the previous one (rebuilt lazily after a restart).
   */
  messages(projectId: string): MessagesState {
    const prev = this.folds.get(projectId) ?? emptyMessages();
    const next = foldMessages(this.o.store.list({ projectId, after: prev.lastId, types: MESSAGE_FOLD_TYPES }), prev);
    this.folds.set(projectId, next);
    return next;
  }

  /**
   * The message tools' one send path (design spec §2.4). Checks, in order: the recipient (an agent of the sender's
   * project by id, or a thread by exact title; not the sender), that neither it nor the project is archived, that its
   * state takes this kind, the text size, one open question per asker and thread, and a thread's 20 questions or
   * notes to other threads an hour. A note or an update to an agent whose open question the sender has seen is that
   * question's answer (auto-link, §1.3): it may reach a failed or stopped asker, and it is exempt from the sender cap.
   * Desk's revision goes its own way (sendRevision); anything else is delivered, a question tracked, with the tool call
   * that sent it. Each refusal throws the text the model reads; `note` is the tool result (§2.5). The thread
   * message_thread tool refuses Desk as a target itself (§2.4 check 2).
   */
  send(input: SendInput): SendResult {
    const from = this.requireAgent(input.from);
    const to = this.resolveRecipient(from.project_id, input.to);
    if (to.id === from.id) throw new Error('That is you.');
    const name = nameOf(to);
    if (to.archived_at || this.archiving.has(to.id)) throw new Error(`${name} is archived.`);
    if (getProject(this.o.store.db, from.project_id)?.archived_at) throw new Error('This project is archived.');
    const s = this.messages(from.project_id);
    const answers = this.answerable(s, from, to, input.kind);
    // A stopped Desk takes every message: it reads them when the user resumes it. A stopped thread (or one whose
    // stopped run is still winding down) takes only the answer to its own open question (a failed or stopped asker
    // keeps its questions open, §1.3).
    if (to.role === 'thread' && !answers) {
      if (to.status === 'cancelled' || this.stoppedAt.has(to.id)) throw new Error(`${name} was stopped; it cannot receive messages.`);
      if (input.kind === 'note' && (to.status === 'done' || to.status === 'failed')) {
        throw new Error(
          from.role === 'desk'
            ? `${name} has finished; its result is final. Send kind "question" to ask about its work, "revision" if it fell short of its brief, or spawn a new thread whose brief points at its result or branch.`
            : `${name} has finished; its result is final. Send kind "question" to ask about its work, or tell Desk (message_desk) if its work needs to change.`,
        );
      }
    }
    if (input.text.length > MAX_MESSAGE_CHARS) {
      throw new Error(`Messages are limited to ${MAX_MESSAGE_CHARS} characters; publish long content with library_publish and send its path.`);
    }
    if (input.kind === 'question' && to.role === 'thread') {
      const asked = openFrom(s, from.id).find((q) => q.to === to.id);
      if (asked) throw new Error(`You already asked ${name} #${asked.id} and it has not answered. Wait for the answer (wait_for_reply) or send a note.`);
    }
    if (from.role === 'thread' && to.role === 'thread' && !answers && (input.kind === 'question' || input.kind === 'note')) {
      const since = new Date(Date.now() - HOUR_MS).toISOString();
      if (sentSince(s, from.id, since).length >= SENDER_CAP) {
        throw new Error(
          `You have sent ${SENDER_CAP} questions or notes to other threads this hour. Stop and ask Desk (message_desk) to coordinate, or keep working with what you have.`,
        );
      }
    }
    if (answers) {
      const id = this.answer(answers.id, from.id, input.text, { toolCallId: input.toolCallId });
      if (id !== null) {
        const held = this.heldByPause(to.id) ? ` (${PAUSED_NOTE})` : '';
        return { id, kind: 'answer', replyTo: answers.id, note: `Sent #${id} to ${name} as the answer to its question #${answers.id}${held}.` };
      }
    }
    if (input.kind === 'revision') return this.sendRevision(from, to, input);
    const next = this.sendOutcome(from, to, input.kind);
    const id = this.deliver(from.id, to.id, input.kind, input.text, { tracked: input.kind === 'question', toolCallId: input.toolCallId });
    // The pause kept the recipient from being woken (maybe this very send paused the project): it reads this after the resume.
    if (this.heldByPause(to.id)) return { id, kind: input.kind, note: `Sent ${input.kind} #${id} to ${name} (${PAUSED_NOTE}).` };
    // Waiting pays off when the recipient reads the question in a full run; an answer run or a held recipient is said as such.
    const hint = from.role === 'thread' && input.kind === 'question' && next.readsInRun ? ' Call wait_for_reply to pause until it answers, or keep working.' : '';
    return { id, kind: input.kind, note: `Sent ${input.kind} #${id} to ${name} (${next.text}).${hint}` };
  }

  updateProject(projectId: string, patch: { name?: string; goal?: string; instructions?: string; settings?: ProjectSettingsPatch }): void {
    this.requireOpenProject(projectId);
    this.o.store.append({ project_id: projectId, agent_id: null, type: 'project.updated', payload: patch });
  }

  /** Archives the project (data is kept), then stops its agents and services. Archived first, so no stop wakes anyone. */
  archiveProject(projectId: string): void {
    this.requireOpenProject(projectId);
    this.o.store.append({ project_id: projectId, agent_id: null, type: 'project.archived', payload: {} });
    for (const agent of listAgents(this.o.store.db, projectId)) {
      if (!TERMINAL.has(agent.status) || this.scheduler.isActive(agent.id)) this.stopAgent(agent.id, { by: agent.id, reason: 'Project archived' });
    }
    this.stopServicesOf(projectId, 'project_archived').catch((err) => this.reportError(err, `stopping services of ${projectId}`));
  }

  /** Validates model ids, and reasoning levels against the model they apply to, then applies a settings patch. */
  updateSettings(projectId: string, patch: ProjectSettingsPatch): void {
    for (const key of ['desk_model', 'thread_model', 'fallback_model'] as const) {
      const model = patch[key];
      if (model) this.o.models.get(model);
    }
    const current = getProject(this.o.store.db, projectId)?.settings;
    for (const [effortKey, modelKey] of [
      ['desk_reasoning_effort', 'desk_model'],
      ['thread_reasoning_effort', 'thread_model'],
    ] as const) {
      const effort = patch[effortKey];
      const model = patch[modelKey] ?? current?.[modelKey];
      if (!effort || !model) continue;
      const levels = this.o.models.get(model).reasoning_efforts;
      if (!levels.includes(effort)) throw new ValidationError(`${model} does not take reasoning effort "${effort}"${levels.length ? ` (it takes: ${levels.join(', ')})` : ' (it takes none)'}`);
    }
    this.updateProject(projectId, { settings: patch });
  }

  /** Creates a thread for Desk: its own workspace (git worktree for a repo source, else a scratch dir), then starts it. */
  async spawnThread(
    parentId: string,
    input: { title: string; brief: string; gitSourceId?: string; model?: string; reasoningEffort?: ReasoningEffort; skills?: string[] },
  ): Promise<string> {
    const parent = this.requireAgent(parentId);
    const project = getProject(this.o.store.db, parent.project_id)!;
    const model = input.model ?? project.settings.thread_model;
    const info = this.o.models.get(model);
    if (input.reasoningEffort && !info.reasoning_efforts.includes(input.reasoningEffort)) {
      throw new ValidationError(
        `${model} does not take reasoning effort "${input.reasoningEffort}"${info.reasoning_efforts.length ? ` (it takes: ${info.reasoning_efforts.join(', ')})` : ' (it takes none)'}`,
      );
    }
    const skills = this.requireUsableSkills(project.id, input.skills ?? []);
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
        ...(input.reasoningEffort ? { reasoning_effort: input.reasoningEffort } : {}),
        title: input.title,
        brief: input.brief,
        workspace_path: workspacePath,
        parent_id: parent.id,
        git: ws.git && gitSource ? { source_id: gitSource.id, ...ws.git } : null,
        ...(skills.length ? { skills } : {}),
      },
    });
    this.deliver(parent.id, id, 'start', 'Begin your assignment.');
    return id;
  }

  /**
   * Archives a finished thread (design spec §3.5): from the first step nothing new reaches it, the questions to it are
   * closed, an answer run in flight ends, then its services stop, its workspace goes (keeping any git branch) and it is
   * marked archived. If a step fails, it stays unarchived and can be archived again.
   */
  async archiveThread(threadId: string): Promise<void> {
    const t = this.requireAgent(threadId);
    if (t.role !== 'thread') throw new ValidationError('Only threads can be archived');
    if (!TERMINAL.has(t.status)) throw new ConflictError(`Thread ${threadId} is ${t.status}; stop it before archiving`);
    if (this.archiving.has(t.id)) throw new ConflictError(`Thread ${threadId} is already being archived`);
    this.archiving.add(t.id);
    try {
      // Closed before the stop, so the stopped job's afterRun finds nothing open and starts nothing.
      this.closeQuestionsTo(t, WHY.archived);
      await this.scheduler.stopAndWait(t.id);
      await this.stopServicesOf(t.project_id, 'thread_archived', t.id);
      if (t.workspace_path) {
        const source = t.git_source_id ? getSource(this.o.store.db, t.git_source_id) : undefined;
        await removeWorkspace({ path: t.workspace_path, gitSourcePath: source?.path ?? null });
      }
      this.o.store.append({ project_id: t.project_id, agent_id: t.id, type: 'agent.archived', payload: {} });
    } finally {
      this.archiving.delete(t.id);
    }
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

  // ── skills ───────────────────────────────────────────────────────────

  /** Skills visible to a project (project skills shadow global ones), or global skills only. */
  listSkills(projectId?: string): SkillSummary[] {
    if (projectId) this.requireOpenProject(projectId);
    return this.skills.list(projectId);
  }

  /** A skill by scope, or as resolved for a project (project first, then global) when no scope is given. */
  getSkill(name: string, opts: { scope?: SkillScope; projectId?: string } = {}): SkillDetail {
    const scope = opts.scope ?? this.skills.resolve(name, opts.projectId)?.scope;
    const skill = scope ? this.skills.get(scope, name, opts.projectId) : undefined;
    if (!skill) throw new NotFoundError(`Unknown skill: ${name}`);
    return skill;
  }

  /**
   * Creates or refines a skill (the previous version is kept in history).
   * `projectId` in meta attributes a global change to the project it was made from.
   */
  saveSkill(
    input: SkillSaveInput,
    meta: { origin?: string; changeNote?: string; projectId?: string; agentId?: string } = {},
  ): { version: number; dir: string; created: boolean; description: string } {
    if (input.scope === 'project') this.requireOpenProject(input.projectId ?? '');
    const r = this.skills.save(input);
    this.o.store.append({
      project_id: input.projectId ?? meta.projectId ?? GLOBAL_PROJECT_ID,
      agent_id: meta.agentId ?? null,
      type: 'skill.saved',
      payload: {
        scope: input.scope,
        name: input.name,
        version: r.version,
        description: r.description,
        origin: meta.origin ?? 'user',
        change_note: meta.changeNote ?? (r.created ? 'Created' : 'Updated'),
      },
    });
    return r;
  }

  deleteSkill(scope: SkillScope, name: string, projectId?: string, meta: { origin?: string; projectId?: string; agentId?: string } = {}): void {
    if (scope === 'project') this.requireOpenProject(projectId ?? '');
    this.skills.delete(scope, name, projectId);
    this.o.skillEnv?.remove({ scope, name, ...(scope === 'project' && projectId ? { projectId } : {}) });
    this.o.store.append({
      project_id: projectId ?? meta.projectId ?? GLOBAL_PROJECT_ID,
      agent_id: meta.agentId ?? null,
      type: 'skill.deleted',
      payload: { scope, name, origin: meta.origin ?? 'user' },
    });
  }

  /** Versions of a skill, each with its change note, origin and time from the `skill.saved` log. */
  skillHistory(scope: SkillScope, name: string, projectId?: string) {
    const notes = new Map<number, { change_note: string; origin: string; ts: string }>();
    // A global skill saved by an agent is logged under that agent's project, so global history reads the whole log.
    for (const e of this.o.store.list({ ...(scope === 'project' && projectId ? { projectId } : {}), types: ['skill.saved'] })) {
      if (e.type === 'skill.saved' && e.payload.scope === scope && e.payload.name === name) notes.set(e.payload.version, { change_note: e.payload.change_note, origin: e.payload.origin, ts: e.ts });
    }
    return this.skills.history(scope, name, projectId).map((h) => {
      const n = notes.get(h.version);
      return { ...h, change_note: n?.change_note ?? '', origin: n?.origin ?? null, ts: n?.ts ?? null };
    });
  }

  /** A skill as it was at `version`; the scope resolves like getSkill when omitted. */
  getSkillVersion(name: string, version: number, opts: { scope?: SkillScope; projectId?: string } = {}): SkillDetail {
    const scopes: SkillScope[] = opts.scope ? [opts.scope] : opts.projectId ? ['project', 'global'] : ['global'];
    for (const scope of scopes) {
      const v = this.skills.getVersion(scope, name, version, scope === 'project' ? opts.projectId : undefined);
      if (v) return v;
    }
    throw new NotFoundError(`Skill ${name} has no version ${version}`);
  }

  restoreSkill(scope: SkillScope, name: string, version: number, projectId?: string) {
    const r = this.skills.restore(scope, name, version, projectId);
    this.o.store.append({
      project_id: projectId ?? GLOBAL_PROJECT_ID,
      agent_id: null,
      type: 'skill.saved',
      payload: { scope, name, version: r.version, description: r.description, origin: 'user', change_note: `Restored version ${version}` },
    });
    return r;
  }

  /** Imports a skill directory from disk (e.g. a Claude Code skill); its name comes from its SKILL.md unless given. */
  importSkill(path: string, opts: { scope: SkillScope; projectId?: string; name?: string }) {
    if (!existsSync(join(path, 'SKILL.md'))) throw new ValidationError(`${path} has no SKILL.md`);
    const name = opts.name ?? SkillStore.declaredName(path);
    return this.saveSkill(
      { scope: opts.scope, name, fromDir: path, ...(opts.projectId ? { projectId: opts.projectId } : {}) },
      { origin: 'user', changeNote: `Imported from ${path}` },
    );
  }

  /** See RuntimeServices.skillEnv. */
  skillEnv(agentId: string, only?: { scope: SkillScope; name: string }): { bins: string[]; vars: Record<string, string>; blocked: string | null; note: string | null } {
    const out = { bins: [] as string[], vars: {} as Record<string, string>, blocked: null as string | null, note: null as string | null };
    const provider = this.o.skillEnv;
    if (!provider) return out;
    const agent = this.requireAgent(agentId);
    const refOf = (s: { scope: SkillScope; name: string }) => ({ scope: s.scope, name: s.name, ...(s.scope === 'project' ? { projectId: agent.project_id } : {}) });
    if (only) {
      const e = provider.env(refOf(only));
      if (e.state === 'preparing') out.blocked = `${only.name}'s runtime is still being set up. Try again in a minute.`;
      else if (e.state === 'failed') out.blocked = `${only.name}'s runtime is not ready: ${e.reason ?? 'setup failed'}. Ask the user to retry it in Skills.`;
      return { ...out, bins: e.bins, vars: e.vars, note: e.note };
    }
    for (const name of agent.active_skills) {
      const skill = this.skills.resolve(name, agent.project_id);
      if (!skill) continue;
      const e = provider.env(refOf(skill));
      if (e.state !== 'ready') continue;
      out.bins.push(...e.bins);
      // Earlier skills come first on PATH, so their variables win too (VIRTUAL_ENV and PLAYWRIGHT_BROWSERS_PATH match the python that runs).
      out.vars = { ...e.vars, ...out.vars };
    }
    return out;
  }

  /** Adds skills to an agent's active set (their instructions join its system prompt from its next run). */
  activateSkills(agentId: string, names: string[]): SkillSummary[] {
    const agent = this.requireAgent(agentId);
    const resolved = this.requireUsableSkills(agent.project_id, names).map((n) => this.skills.resolve(n, agent.project_id)!);
    const next = [...new Set([...agent.active_skills, ...names])];
    if (next.length !== agent.active_skills.length) {
      this.o.store.append({ project_id: agent.project_id, agent_id: agent.id, type: 'agent.skills_changed', payload: { skills: next } });
    }
    return resolved;
  }

  /** Validates a skill draft directory: it must be inside the workspace of an agent of the project. */
  skillDraftDir(projectId: string, path: string): string {
    let real: string;
    try {
      real = realpathSync(path);
    } catch {
      throw new ValidationError(`No such directory: ${path}`);
    }
    const inside = listAgents(this.o.store.db, projectId).some((a) => {
      if (!a.workspace_path || !existsSync(a.workspace_path)) return false;
      const ws = realpathSync(a.workspace_path);
      return real === ws || real.startsWith(ws + sep);
    });
    if (!inside) throw new ValidationError(`${path} is not inside a workspace of this project`);
    return real;
  }

  private requireUsableSkills(projectId: string, names: string[]): string[] {
    for (const name of names) {
      const skill = this.skills.resolve(name, projectId);
      if (!skill) throw new ValidationError(`Unknown skill: ${name}`);
      if (skill.error) throw new ValidationError(`Skill ${name} is broken: ${skill.error}`);
    }
    return [...new Set(names)];
  }

  // ── messaging & control ──────────────────────────────────────────────

  // ── services ─────────────────────────────────────────────────────

  serviceLogFile(projectId: string, serviceId: string): string {
    return join(this.projectDir(projectId), 'services', `${serviceId}.log`);
  }

  /**
   * Starts a project service in a thread's workspace, sandboxed like the thread's own shell tools. A name that is
   * already running is stopped first and started again under the same id.
   */
  async startService(
    projectId: string,
    input: { name: string; command: string; cwd?: string; threadId?: string; sourceId?: string; by: string },
  ): Promise<ServiceRow> {
    this.requireOpenProject(projectId);
    const name = ServiceName.safeParse(input.name);
    if (!name.success) throw new ValidationError(`Invalid service name "${input.name}": ${name.error.issues[0]?.message ?? 'invalid'}`);
    const command = input.command.trim();
    if (!command) throw new ValidationError('A service needs a command');
    const place = this.servicePlace(projectId, input);
    const workspace = place.root;
    const cwd = await resolveInside(input.cwd ?? '.', [workspace], workspace).catch(() => {
      throw new ValidationError(`cwd must be a directory inside ${place.label}: ${input.cwd}`);
    });
    if (!existsSync(cwd) || !statSync(cwd).isDirectory()) throw new ValidationError(`cwd must be a directory inside ${place.label}: ${input.cwd}`);

    const existing = findService(this.o.store.db, projectId, name.data);
    if (existing?.status === 'running') await this.stopServiceRun(existing, input.by, 'restart');
    const serviceId = existing?.id ?? `svc_${newId()}`;
    const env = withSkillEnv(scrubbedEnv(workspace), this.skillEnv(place.agent.id));
    const sandbox = { enabled: await this.sandboxAvailable(), writable: [workspace, ...place.writable, ...this.writeRoots(place.agent)], guard: this.guard, gitDirs: this.gitDirs(place.agent) };
    const relCwd = cwd === workspace ? '.' : cwd.slice(workspace.length + 1);
    const pid = this.serviceProcs.start(serviceId, {
      command,
      cwd,
      env,
      sandbox,
      logFile: this.serviceLogFile(projectId, serviceId),
      header: `── started ${new Date().toISOString()} · ${relCwd === '.' ? '' : `${relCwd} · `}${command} ──`,
    });
    this.o.store.append({
      project_id: projectId,
      agent_id: null,
      type: 'service.started',
      payload: { service_id: serviceId, name: name.data, command, cwd: relCwd, workspace_agent_id: place.agent.id, source_id: place.sourceId, pid, by: input.by },
    });
    return getService(this.o.store.db, serviceId)!;
  }

  /** Stops a running service (a stopped or exited one is returned unchanged). */
  async stopService(serviceId: string, by: string): Promise<ServiceRow> {
    const s = this.requireService(serviceId);
    if (s.status === 'running') await this.stopServiceRun(s, by, 'requested');
    return getService(this.o.store.db, serviceId)!;
  }

  /** Runs a service's recorded command again in its recorded workspace (stopping the current run first). */
  async restartService(serviceId: string, by: string): Promise<ServiceRow> {
    const s = this.requireService(serviceId);
    return this.startService(s.project_id, { name: s.name, command: s.command, cwd: s.cwd, ...(s.source_id ? { sourceId: s.source_id } : { threadId: s.agent_id }), by });
  }

  serviceLogs(serviceId: string, lines: number): { text: string; truncated: boolean } {
    const s = this.requireService(serviceId);
    return tailLog(this.serviceLogFile(s.project_id, s.id), Math.max(1, Math.min(lines, 2000)));
  }

  /** Records the stop at once (the UI updates), then waits for the process group to close so its port is free. */
  private stopServiceRun(s: ServiceRow, by: string, reason: ServiceStopReason): Promise<void> {
    this.o.store.append({ project_id: s.project_id, agent_id: null, type: 'service.stopped', payload: { service_id: s.id, by, reason } });
    return this.serviceProcs.stop(s.id);
  }

  /** Stops the running services of a project, or only those in one thread's workspace. */
  private stopServicesOf(projectId: string, reason: ServiceStopReason, threadId?: string): Promise<void> {
    const running = listServices(this.o.store.db, projectId).filter((s) => s.status === 'running' && (!threadId || (s.agent_id === threadId && !s.source_id)));
    return Promise.all(running.map((s) => this.stopServiceRun(s, 'system', reason))).then(() => undefined);
  }

  private reportError(err: unknown, context: string): void {
    (this.o.onError ?? ((e, c) => console.error(`[desk] ${c}:`, e)))(err, context);
  }

  private requireService(serviceId: string): ServiceRow {
    const s = getService(this.o.store.db, serviceId);
    if (!s) throw new NotFoundError(`Unknown service: ${serviceId}`);
    return s;
  }

  /**
   * Where a service runs: a thread's workspace, or a project source folder that allows agents to write (then its
   * environment follows the agent that started it, or Desk when the user did).
   */
  private servicePlace(
    projectId: string,
    input: { threadId?: string; sourceId?: string; by: string },
  ): { root: string; label: string; agent: AgentRow; sourceId: string | null; writable: string[] } {
    if (input.sourceId) {
      const source = getSource(this.o.store.db, input.sourceId);
      if (!source || source.project_id !== projectId) throw new ValidationError(`Unknown source in this project: ${input.sourceId}`);
      if (!source.agent_write) throw new ConflictError(`Agents may not write to ${source.label}; turn it on in the project's Settings → Sources`);
      if (!existsSync(source.path)) throw new ConflictError(`Source folder is missing: ${source.path}`);
      const unsafe = this.unsafeSource(realpathSync(source.path));
      if (unsafe) throw new ConflictError(`Services cannot run in ${source.label}: it is ${unsafe}`);
      const starter = input.by.startsWith('agent:') ? getAgent(this.o.store.db, input.by.slice(6)) : undefined;
      const agent = starter && starter.project_id === projectId ? starter : getDeskAgent(this.o.store.db, projectId);
      if (!agent) throw new NotFoundError(`Project ${projectId} has no Desk agent`);
      return { root: realpathSync(source.path), label: `${source.label} (${source.path})`, agent, sourceId: source.id, writable: [] };
    }
    if (!input.threadId) throw new ValidationError('A service needs a thread workspace or a project source to run in');
    const thread = this.serviceWorkspaceOwner(projectId, input.threadId);
    return { root: realpathSync(thread.workspace_path!), label: "the thread's workspace", agent: thread, sourceId: null, writable: thread.git_common_dir ? [thread.git_common_dir] : [] };
  }

  private serviceWorkspaceOwner(projectId: string, threadId: string): AgentRow {
    const t = getAgent(this.o.store.db, threadId);
    if (!t || t.project_id !== projectId || t.role !== 'thread') throw new ValidationError(`Unknown thread in this project: ${threadId}`);
    if (t.archived_at) throw new ConflictError(`Thread ${threadId} is archived; its workspace is gone`);
    if (!t.workspace_path || !existsSync(t.workspace_path)) throw new ConflictError(`Thread ${threadId} has no workspace`);
    return t;
  }

  /**
   * The user's message to an agent. With `question`, the user's Ask to a thread: an idle, done or failed thread
   * answers it in an answer run without reopening (design spec §4.8); any other thread reads it as a message.
   * Refused (409) for an archived thread and for any agent of an archived project, and (400) for an Ask to Desk. The
   * user's message ends a pause of the project's automatic wakes (design spec §5.4).
   */
  sendMessage(agentId: string, text: string, opts: { question?: boolean } = {}): void {
    const agent = this.requireAgent(agentId);
    if (agent.archived_at || this.archiving.has(agentId)) throw new ConflictError(`Thread ${agentId} is archived`);
    this.requireOpenProject(agent.project_id);
    if (opts.question && agent.role !== 'thread') throw new ValidationError('Only a thread can be asked a question; write to Desk instead');
    this.o.store.append({
      project_id: agent.project_id,
      agent_id: agentId,
      type: 'message.user',
      payload: { text, ...(opts.question ? { question: true as const } : {}) },
    });
    // The user is back: automatic wakes resume and every agent of the project decides again.
    this.resume(agent.project_id);
    this.wake(agentId);
  }

  /** User-initiated stop. */
  stop(agentId: string): void {
    this.stopAgent(agentId);
  }

  /**
   * Stops an agent: kills its jobs, dequeues or aborts its job and denies its pending approvals (design spec §3.5).
   * A running full run ends `cancelled` by itself. Otherwise (no job, a queued job, or an answer job, which counts as
   * no job) an agent that has not finished is cancelled here. Stopping a thread closes the open questions to it;
   * stopping Desk closes nothing. `by` is the agent that stopped it (Desk, or the project's archive): the cancellation
   * is not reported back to it.
   */
  stopAgent(agentId: string, opts: { by?: string; reason?: string } = {}): void {
    const agent = this.requireAgent(agentId);
    this.jobs.killAll(agentId);
    const { state, job } = this.scheduler.stop(agentId);
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
    if (state === 'running' && job?.kind === 'run') {
      // The run's own ending appends `cancelled`; afterRun's notifyParent consumes this entry.
      if (opts.by) this.silentStops.add(agentId);
      if (!this.stoppedAt.has(agentId)) this.stoppedAt.set(agentId, lastEvent(this.o.store.db, agentId)?.id ?? 0);
    } else if (!TERMINAL.has(this.requireAgent(agentId).status)) {
      this.o.store.append({
        project_id: agent.project_id,
        agent_id: agentId,
        type: 'agent.status_changed',
        payload: { status: 'cancelled', reason: opts.reason ?? (state === 'queued' && job?.kind === 'run' ? 'Stopped before starting' : 'Stopped') },
      });
      if (!opts.by) this.notifyParent(this.requireAgent(agentId));
    }
    // A stopped thread answers no one: each asker gets the runtime's closure. An aborted answer run finds its question
    // closed and appends only run.finished.
    this.closeQuestionsTo(this.requireAgent(agentId), WHY.stopped);
  }

  /**
   * Reports running and waiting threads with no activity for `thresholdMs` to their Desk, once per stall; a waiting
   * thread's notice names what it waits on. Paused projects are skipped: their agents wait for the user on purpose
   * (design spec §5.4). Returns the reported thread ids.
   */
  checkStalls(now = Date.now(), thresholdMs = 15 * 60_000): string[] {
    const reported: string[] = [];
    for (const t of listActiveThreads(this.o.store.db)) {
      if (!t.parent_id || this.paused.has(t.project_id)) continue;
      if (pendingApprovalsFor(this.o.store.db, t.id).length) continue;
      const last = lastEvent(this.o.store.db, t.id);
      if (!last || now - Date.parse(last.ts) < thresholdMs || this.stallNotified.get(t.id) === last.id) continue;
      this.stallNotified.set(t.id, last.id);
      const minutes = Math.round((now - Date.parse(last.ts)) / 60_000);
      this.deliver(t.id, t.parent_id, 'stalled', `No activity for ${minutes} minutes (status: ${t.status}${this.waitingOn(t, now)}).`);
      reported.push(t.id);
    }
    return reported;
  }

  /**
   * What a waiting thread waits on, for its stall notice: `; waiting on "Frontend"'s answer to #123 for 16 minutes`
   * (its oldest open question), or `your answer` when it asked its Desk. '' for any other thread (design spec §3.6).
   */
  private waitingOn(t: AgentRow, now: number): string {
    if (t.status !== 'waiting') return '';
    const s = this.messages(t.project_id);
    const q = openFrom(s, t.id)[0];
    if (!q) return '';
    const to = s.agents[q.to];
    const who = to?.role === 'desk' ? 'your' : `"${sanitizeLabel(to?.title ?? 'untitled')}"'s`;
    return `; waiting on ${who} answer to #${q.id} for ${Math.round((now - Date.parse(q.ts)) / 60_000)} minutes`;
  }

  /**
   * Resolves a pending approval: runs (or denies) the held tool call, then resumes the agent. Until the call's
   * `tool.result` exists the agent's conversation ends in a call without a result, so `resolving` holds every wake.
   * A shutdown aborts the call; recover() records `interrupted` for one an unclean exit cut off.
   */
  async resolveApproval(approvalId: string, decision: 'approved' | 'denied', opts: { by?: 'user' | 'desk'; note?: string } = {}): Promise<void> {
    const { store } = this.o;
    const ap = getApproval(store.db, approvalId);
    if (!ap) throw new NotFoundError(`Unknown approval: ${approvalId}`);
    if (ap.status !== 'pending') throw new ConflictError(`Approval ${approvalId} is already resolved (${ap.status})`);
    const by = opts.by ?? 'user';
    const base = { project_id: ap.project_id, agent_id: ap.agent_id };
    let recorded!: () => void;
    const resolution = { controller: new AbortController(), recorded: new Promise<void>((r) => (recorded = r)) };
    if (this.shuttingDown) resolution.controller.abort(SHUTDOWN_REASON);
    this.resolutions.add(resolution);
    this.resolving.set(ap.agent_id, (this.resolving.get(ap.agent_id) ?? 0) + 1);
    try {
      store.append({ ...base, type: 'approval.resolved', payload: { approval_id: ap.id, decision, resolved_by: by, ...(opts.note ? { note: opts.note } : {}) } });

      const agent = this.requireAgent(ap.agent_id);
      let result: ToolResult;
      if (decision === 'approved') {
        const prepared = prepareToolCall(this.toolsFor(agent), { id: ap.tool_call_id, name: ap.tool, arguments: ap.arguments });
        result = prepared.ok
          ? await runPreparedTool(prepared.tool, prepared.input, await this.toolContext(agent, ap.run_id, ap.tool_call_id, resolution.controller.signal))
          : prepared.result;
      } else {
        result = { status: 'denied', content: `Denied by ${by}${opts.note ? `: ${opts.note}` : ''}` };
      }
      store.append({
        ...base,
        type: 'tool.result',
        payload: {
          run_id: ap.run_id,
          tool_call_id: ap.tool_call_id,
          name: ap.tool,
          status: result.status,
          content: result.content,
          ...(result.images?.length ? { images: result.images } : {}),
        },
      });
    } finally {
      const left = (this.resolving.get(ap.agent_id) ?? 1) - 1;
      if (left > 0) this.resolving.set(ap.agent_id, left);
      else this.resolving.delete(ap.agent_id);
      this.resolutions.delete(resolution);
      recorded();
    }
    // Two approvals of one step can resolve at once: the last to finish resumes the agent.
    if (this.resolving.has(ap.agent_id)) return;
    this.resumeAfterApproval(ap.agent_id);
  }

  /**
   * Once an approval's call has its result: resumes an agent still waiting with no approval left (the direct resume),
   * otherwise decides as any wake, so a message that arrived during the call is decided then. Returns whether it scheduled.
   */
  private resumeAfterApproval(agentId: string): boolean {
    const agent = this.requireAgent(agentId);
    if (agent.status !== 'waiting' || pendingApprovalsFor(this.o.store.db, agentId).length > 0 || this.scheduler.isActive(agentId)) return this.wake(agentId);
    this.schedule(agent);
    return true;
  }

  whenIdle(): Promise<void> {
    return this.scheduler.whenIdle();
  }

  /**
   * Stops scheduling, interrupts running agents resumably, kills background jobs and stops services. Approved calls
   * still running are aborted like a run's calls, and their results recorded, so no call is left without one.
   */
  async shutdown(): Promise<void> {
    this.shuttingDown = true;
    this.scheduler.stopAll(SHUTDOWN_REASON);
    const resolutions = [...this.resolutions];
    for (const r of resolutions) r.controller.abort(SHUTDOWN_REASON);
    this.jobs.killEverything();
    for (const s of listRunningServices(this.o.store.db)) {
      this.o.store.append({ project_id: s.project_id, agent_id: null, type: 'service.stopped', payload: { service_id: s.id, by: 'system', reason: 'daemon_shutdown' } });
    }
    await Promise.all([this.serviceProcs.stopAll(), this.scheduler.whenIdle(), ...resolutions.map((r) => r.recorded)]);
    this.proxy?.close();
  }

  /**
   * On startup: re-derives the projects whose automatic wakes are paused, repairs runs and approved calls cut off by a
   * crash (unfinished tool calls become `interrupted`), then wakes every live agent through wakeDecision. Returns the
   * rescheduled agent ids.
   */
  recover(): string[] {
    // A pause outlives a restart until the user ends it; the wake windows start empty (design spec §5.4).
    for (const p of listProjects(this.o.store.db)) if (pausedNotice(this.o.store.db, p.id)) this.paused.add(p.id);
    // Services still marked running were cut off by an unclean exit: reap an orphan still holding its port, mark stopped.
    for (const s of listRunningServices(this.o.store.db)) {
      if (this.serviceProcs.isRunning(s.id)) continue;
      reapOrphan(s.pid, s.command);
      this.o.store.append({ project_id: s.project_id, agent_id: null, type: 'service.stopped', payload: { service_id: s.id, by: 'system', reason: 'daemon_restart' } });
    }
    const repairedByProject = new Map<string, number>();
    const repaired = (projectId: string) => repairedByProject.set(projectId, (repairedByProject.get(projectId) ?? 0) + 1);
    // First the calls resolveApproval was running (their run had already finished), then the runs themselves.
    const approvalsRepaired = new Set<string>();
    for (const agent of listLiveAgents(this.o.store.db)) {
      if (!this.repairResolvedApprovals(agent)) continue;
      approvalsRepaired.add(agent.id);
      repaired(agent.project_id);
    }
    // Every live agent whose last run has no end: a full run (left `running`) or an answer run (any status).
    for (const agent of listLiveAgents(this.o.store.db)) {
      if (this.repairCrashedRun(agent)) repaired(agent.project_id);
    }
    for (const [projectId, count] of repairedByProject) {
      this.o.store.append({
        project_id: projectId,
        agent_id: null,
        type: 'system.notice',
        payload: { level: 'warning', code: 'daemon_restart', message: `Recovered ${count} run(s) interrupted by an unclean daemon shutdown.` },
      });
    }

    // Every live agent gets the decision it would get at any other time: queued ones resume, stopped ones wait for the
    // user, and one whose approved call was repaired resumes as resolveApproval would have resumed it.
    const resumed: string[] = [];
    for (const agent of listLiveAgents(this.o.store.db)) {
      if (approvalsRepaired.has(agent.id) ? this.resumeAfterApproval(agent.id) : this.wake(agent.id)) resumed.push(agent.id);
    }
    return resumed;
  }

  /**
   * Gives a result to each call whose approval was resolved but whose `tool.result` was never appended: the daemon
   * exited while resolveApproval ran it. An approved call's outcome is unknown, so it is recorded `interrupted`, never
   * run again. Returns whether it repaired anything.
   */
  private repairResolvedApprovals(agent: AgentRow): boolean {
    const { store } = this.o;
    // An approval is resolved after the run that requested it finished, and nothing runs the agent before its result.
    const after = lastEvent(store.db, agent.id, 'run.finished')?.id ?? 0;
    const since = store.list({ agentId: agent.id, after, types: ['approval.resolved', 'tool.result'] });
    const settled = new Set(since.flatMap((e) => (e.type === 'tool.result' ? [e.payload.tool_call_id] : [])));
    const missing = since.flatMap((e) => {
      if (e.type !== 'approval.resolved') return [];
      const ap = getApproval(store.db, e.payload.approval_id);
      if (!ap || settled.has(ap.tool_call_id)) return [];
      const { decision, resolved_by, note } = e.payload;
      return [
        {
          project_id: agent.project_id,
          agent_id: agent.id,
          type: 'tool.result' as const,
          payload: {
            run_id: ap.run_id,
            tool_call_id: ap.tool_call_id,
            name: ap.tool,
            ...(decision === 'approved'
              ? { status: 'interrupted' as const, content: INTERRUPTED_BY_RESTART }
              : { status: 'denied' as const, content: `Denied by ${resolved_by}${note ? `: ${note}` : ''}` }),
          },
        },
      ];
    });
    if (missing.length) store.append(missing);
    return missing.length > 0;
  }

  /**
   * Finishes the agent's last run if a crash cut it off (`run.started` with no later `run.finished`), and never
   * re-executes calls whose outcome is unknown. A full run leaves the agent `queued`, or `waiting` on its approvals. An
   * answer run changes no status and closes its question with the restart closure, unless it already has an answer
   * (design spec §3.6). Returns whether it repaired a run.
   */
  private repairCrashedRun(agent: AgentRow): boolean {
    const { store } = this.o;
    const started = lastEvent(store.db, agent.id, 'run.started');
    if (started?.type !== 'run.started') return false;
    const finished = lastEvent(store.db, agent.id, 'run.finished');
    if (finished && finished.id > started.id) return false;
    const runId = started.payload.run_id;
    const since = store.list({ agentId: agent.id, after: started.id, types: ['tool.call', 'tool.result', 'approval.requested'] });
    const settled = new Set(since.flatMap((e) => (e.type === 'tool.result' || e.type === 'approval.requested' ? [e.payload.tool_call_id] : [])));
    const base = { project_id: agent.project_id, agent_id: agent.id };
    const interrupted = since.flatMap((e): EventInput[] =>
      e.type === 'tool.call' && !settled.has(e.payload.tool_call_id)
        ? [
            {
              ...base,
              type: 'tool.result',
              payload: {
                run_id: runId,
                tool_call_id: e.payload.tool_call_id,
                name: e.payload.name,
                status: 'interrupted',
                content: INTERRUPTED_BY_RESTART,
              },
            },
          ]
        : [],
    );
    const end: EventInput = { ...base, type: 'run.finished', payload: { run_id: runId, reason: 'error', detail: 'daemon_restart' } };
    if (started.payload.answering !== undefined) {
      const q = messageById(this.messages(agent.project_id), started.payload.answering);
      store.append([...interrupted, end, ...(q ? this.answerEnding(agent, q, WHY.restart, true) : [])]);
      return true;
    }
    const waiting = pendingApprovalsFor(store.db, agent.id).length > 0;
    store.append([
      ...interrupted,
      end,
      { ...base, type: 'agent.status_changed', payload: { status: waiting ? 'waiting' : 'queued', reason: 'Recovered after daemon restart' } },
    ]);
    return true;
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

  /** The `message.agent` event for a message from `from` to `to`. A thread's label carries its sanitised title. */
  private messageEvent(from: AgentRow, to: AgentRow, kind: AgentMessageKind, text: string, opts: DeliverOptions): EventInput {
    return {
      project_id: to.project_id,
      agent_id: to.id,
      type: 'message.agent',
      payload: {
        from_agent_id: from.id,
        from_label: from.role === 'desk' ? 'Desk' : `thread "${sanitizeLabel(from.title ?? 'untitled')}" (${from.id})`,
        kind,
        text,
        ...(opts.replyTo !== undefined ? { reply_to: opts.replyTo } : {}),
        ...(opts.auto ? { auto: true as const } : {}),
        ...(opts.tracked ? { tracked: true as const } : {}),
        ...(opts.toolCallId ? { tool_call_id: opts.toolCallId } : {}),
      },
    };
  }

  /**
   * answer()'s guard, as the event to append: the answer to `questionId` from its recipient, or null when the question
   * already has an answer, or when `auto` (a closure the runtime writes) and the question is no longer open. The fold
   * is read and the event appended in one turn of the event loop, so no second answer can slip in between.
   */
  private answerEvent(questionId: number, fromAgentId: string, text: string, opts: { auto?: boolean; toolCallId?: string }): EventInput | null {
    const from = this.requireAgent(fromAgentId);
    const q = messageById(this.messages(from.project_id), questionId);
    if (q?.kind !== 'question') throw new NotFoundError(`Unknown question: #${questionId}`);
    if (q.to !== from.id) throw new ValidationError(`Question #${questionId} was not sent to ${from.id}`);
    if (q.answerId !== undefined || (opts.auto && q.state !== 'open')) return null;
    return this.messageEvent(from, this.requireAgent(q.from), 'answer', text, { replyTo: questionId, auto: opts.auto, toolCallId: opts.toolCallId });
  }

  /**
   * The events that close an answer run's question, appended with its `run.finished` (§4.5, §4.6): the answer for the
   * reply `text` (trimmed and clipped; an empty text becomes the "did not answer" closure), or with `auto` the closure
   * `(<title> <text>.)`. answerEvent's guard applies: nothing once the question has an answer, and a closure only while
   * it is open. The user's question gets none: the run's own text is its answer.
   */
  private answerEnding(thread: AgentRow, q: MessageView, text: string, auto = false): EventInput[] {
    if (q.kind !== 'question') return [];
    const answer = auto ? '' : answerText(text);
    const event = answer
      ? this.answerEvent(q.id, thread.id, answer, {})
      : this.answerEvent(q.id, thread.id, closureText(thread.title, auto ? text : WHY.noAnswer), { auto: true });
    return event ? [event] : [];
  }

  /**
   * Closes every open question to a thread with the runtime's `(<title> <why>.)`, through answer(), which wakes each
   * asker (design spec §3.5). Desk's questions are never closed: a stopped Desk leaves them for when the user resumes it.
   */
  private closeQuestionsTo(thread: AgentRow, why: string): void {
    if (thread.role !== 'thread') return;
    for (const q of openTo(this.messages(thread.project_id), thread.id)) this.answer(q.id, thread.id, closureText(thread.title, why), { auto: true });
  }

  /**
   * Whether the pause is what keeps the agent from running now (design spec §2.5): its project is paused, it has no
   * queued or running full run, and wakeDecision would start it for another agent or a lifecycle item. An answer job
   * reads nothing past its question and closes it, so with one in flight the decision is the one that follows it.
   */
  private heldByPause(agentId: string): boolean {
    const agent = this.requireAgent(agentId);
    if (!this.paused.has(agent.project_id)) return false;
    const job = this.scheduler.jobOf(agent.id);
    if (job?.kind === 'run') return false;
    const s = this.wakeState(agent);
    const q = job?.answering;
    const d = wakeDecision(q === undefined ? s : { ...s, pending: s.pending.filter((i) => i.id > q), open: s.open.filter((o) => o.id !== q) });
    return d.kind !== 'none' && (d.trigger === 'agent' || d.trigger === 'lifecycle');
  }

  /** The agent a send names: an agent of the project by id, or a thread by exact title (threadsByRef). */
  private resolveRecipient(projectId: string, ref: string): AgentRow {
    const byId = getAgent(this.o.store.db, ref);
    if (byId) {
      if (byId.project_id !== projectId) throw new Error(`Unknown thread: ${ref}`);
      return byId;
    }
    const titled = threadsByRef(this.o.store.db, projectId, ref);
    if (titled.length > 1) throw new Error(`Several threads are titled "${ref}"; use its id.`);
    if (!titled[0]) throw new Error(`Unknown thread: ${ref}`);
    return titled[0];
  }

  /**
   * The question a message from `from` to `to` answers, if any (design spec §1.3): for a note or an update, `to`'s
   * oldest open question to `from` that `from` has seen (at or below its inbox cursor). Untracked questions have no
   * state, so they are never answered this way; questions, blockers and revisions never answer.
   */
  private answerable(s: MessagesState, from: AgentRow, to: AgentRow, kind: SendInput['kind']): MessageView | undefined {
    if (kind !== 'note' && kind !== 'update') return undefined;
    return openFrom(s, to.id).find((q) => q.to === from.id && q.id <= from.inbox_cursor);
  }

  /** Desk's revision, as before: the review-round limit, then `agent.revision`, then the message that reopens the thread. */
  private sendRevision(from: AgentRow, to: AgentRow, input: SendInput): SendResult {
    if (from.role !== 'desk' || to.role !== 'thread') throw new Error('Only Desk sends revisions.');
    const limit = getProject(this.o.store.db, to.project_id)!.settings.review_rounds;
    if (to.review_round >= limit) {
      throw new Error(`Review round limit (${limit}) reached for ${nameOf(to)}: accept the work with noted caveats or escalate to the user.`);
    }
    const round = to.review_round + 1;
    this.o.store.append({ project_id: to.project_id, agent_id: to.id, type: 'agent.revision', payload: { round, feedback: input.text } });
    const id = this.deliver(from.id, to.id, 'revision', input.text, { toolCallId: input.toolCallId });
    // Held by the pause, the thread reopens only once the user resumes the project.
    const outcome = this.heldByPause(to.id) ? ` (${PAUSED_NOTE})` : '; it has been reopened';
    return { id, kind: 'revision', note: `Sent revision ${round} to ${nameOf(to)}${outcome}.` };
  }

  /**
   * What happens next to a message `from` is sending `to` now, for its tool result (design spec §2.5, following the
   * delivery table §3.3), and whether the recipient reads it in a full run (as opposed to an answer run, or later).
   */
  private sendOutcome(from: AgentRow, to: AgentRow, kind: SendInput['kind']): { text: string; readsInRun: boolean } {
    const held = pendingApprovalsFor(this.o.store.db, to.id).length + (this.resolving.get(to.id) ?? 0) > 0;
    if (to.role === 'desk' && to.status === 'cancelled') return { text: 'stopped by the user: it reads this when the user resumes it', readsInRun: false };
    if (to.status === 'running') return { text: 'running: it sees this at its next step', readsInRun: true };
    if (to.status === 'queued') return { text: 'queued: it sees this when it starts', readsInRun: true };
    if (held) return { text: 'waiting on an approval: it sees this once the approval is decided', readsInRun: false };
    // Desk runs for anything (rule 3).
    if (to.role === 'desk') return { text: `${to.status}: it is woken to read this`, readsInRun: true };
    const state = to.status === 'waiting' ? 'waiting on its own reply' : to.status;
    // Rule 4.6: an idle, done or failed thread answers a question in an answer run; so does a waiting one, unless Desk asks.
    if (kind === 'question' && to.status !== 'waiting') {
      return { text: `${state}: it will be woken to answer from its context; its result stays final`, readsInRun: false };
    }
    if (kind === 'question' && from.role === 'thread') return { text: `${state}: it will be woken just to answer you, and keeps waiting`, readsInRun: false };
    // Rules 4.4 and 4.5: anything from Desk runs a waiting or idle thread.
    if (from.role === 'desk') return { text: `${state}: it is woken to read this`, readsInRun: true };
    if (to.status === 'idle') return { text: 'idle: it reads this when Desk or the user resumes it', readsInRun: false };
    return { text: `${state}: it reads this when it next runs; notes from other threads do not wake it`, readsInRun: false };
  }

  /** Unknown models are assumed to see images; the endpoint answers for itself. */
  private modelSeesImages(model: string): boolean {
    return this.o.models.has(model) ? this.o.models.get(model).vision : true;
  }

  private toolsFor(agent: AgentRow): Tool[] {
    return this.o.toolsFor?.(agent) ?? toolsForRole(agent);
  }

  /** Own workspace + every project source + the project library + skill directories. */
  /** Source folders agents of the project may write to (besides their own workspace). */
  /**
   * Sources agents may write to. Folders `unsafeSource` names never are, even when an older project has one: agents
   * could plant what runs outside the sandbox there (login items, shell and git config) or reach Desk's own files.
   */
  private writeRoots(agent: AgentRow): string[] {
    return listSources(this.o.store.db, agent.project_id)
      .filter((s) => s.agent_write && existsSync(s.path) && !this.unsafeSource(realOrSelf(s.path)))
      .map((s) => s.path);
  }

  /** Why `real` can never be a project source, or null: the filesystem root, the home folder or above, or Desk's data dir or anything inside or around it. */
  private unsafeSource(real: string): string | null {
    const home = realOrSelf(this.o.home ?? homedir());
    if (real === dirname(real)) return 'the filesystem root';
    if (isWithin(home, real)) return real === home ? 'your home folder' : 'a folder that contains your home folder';
    if (isWithin(real, this.guard.dataDir) || isWithin(this.guard.dataDir, real)) return "Desk's data folder, or a folder inside or around it";
    return null;
  }

  /**
   * Git dirs of the repos deskd runs git in on this agent's behalf, which its commands may not reconfigure: its
   * worktree's `.git` link and the repo's common dir, and the `.git` of every git source it may write to (threads are
   * branched from those).
   */
  private gitDirs(agent: AgentRow): string[] {
    const own = agent.git_common_dir && agent.workspace_path ? [agent.git_common_dir, join(agent.workspace_path, '.git')] : [];
    const sources = listSources(this.o.store.db, agent.project_id)
      .filter((s) => s.kind === 'git' && s.agent_write)
      .map((s) => join(s.path, '.git'));
    return [...new Set([...own, ...sources])];
  }

  /** Blocks agents' commands from connecting to `port` (deskd's, once it listens). */
  guardPort(port: number): void {
    if (!this.guard.ports.includes(port)) this.guard.ports.push(port);
  }

  private readRoots(agent: AgentRow): string[] {
    const roots = listSources(this.o.store.db, agent.project_id).map((s) => s.path);
    return [...(agent.workspace_path ? [agent.workspace_path] : []), ...roots, this.libraryDir(agent.project_id), ...this.skills.roots(agent.project_id)];
  }

  private systemPrompt(agent: AgentRow, project: ProjectRow): string {
    if (this.o.systemPrompt) return this.o.systemPrompt(agent, project);
    const provider = this.o.skillEnv;
    const skillNote = (s: { scope: SkillScope; name: string }) => provider?.env({ scope: s.scope, name: s.name, ...(s.scope === 'project' ? { projectId: project.id } : {}) }).note ?? null;
    const ctx = { db: this.o.store.db, agent, project, libraryDir: this.libraryDir(project.id), skills: this.skills, skillNote, messages: this.messages(project.id) };
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
      writeRoots: (a) => this.writeRoots(a),
      guard: this.guard,
      gitDirs: (a) => this.gitDirs(a),
    });
  }

  /**
   * Starts what wakeDecision says the agent should do now (design spec §3.2, §3.4): a full run through schedule(), or
   * an answer job for one question, which appends no status event. A wake for another agent or a lifecycle item first
   * passes the wake budget (admit); if it does not, its items stay stored. Returns whether it enqueued a job.
   */
  private wake(agentId: string): boolean {
    if (this.shuttingDown || this.scheduler.isActive(agentId)) return false;
    const agent = this.requireAgent(agentId);
    const d = wakeDecision(this.wakeState(agent));
    if (d.kind === 'none' || !this.admit(agent.project_id, d.trigger)) return false;
    if (d.kind === 'answer') {
      this.scheduler.enqueue({ agentId: agent.id, projectId: agent.project_id, model: agent.model, role: agent.role, kind: 'answer', answering: d.question });
    } else {
      this.schedule(agent);
    }
    return true;
  }

  /**
   * The wake budgets (design spec §3.4 steps 3–4, §5.4). `user` and `queued` wakes always go through; while the
   * project is paused nothing else does. Any other wake is counted in the project's rolling hour for its trigger:
   * when that hour already holds the budget (wakeBudget for `agent`, lifecycleBudget for `lifecycle`), the project
   * pauses and the wake is dropped.
   */
  private admit(projectId: string, trigger: Trigger): boolean {
    if (trigger === 'user' || trigger === 'queued') return true;
    if (this.paused.has(projectId)) return false;
    let windows = this.wakeWindows.get(projectId);
    if (!windows) this.wakeWindows.set(projectId, (windows = { agent: [], lifecycle: [] }));
    const budget = trigger === 'agent' ? (this.o.wakeBudget ?? 60) : (this.o.lifecycleBudget ?? 150);
    if (takeWake(windows[trigger], Date.now(), budget)) return true;
    this.pause(projectId, trigger, budget);
    return false;
  }

  /**
   * Pauses the project's automatic wakes (design spec §5.4) and tells the user once, with the `wakes_paused` notice
   * that core attention turns into the `paused` item. Only admit calls it, on a project that is not paused yet.
   */
  private pause(projectId: string, trigger: 'agent' | 'lifecycle', budget: number): void {
    this.paused.add(projectId);
    const what = trigger === 'agent' ? `Agents woke each other ${budget} times` : `Agents were woken by thread starts and notices ${budget} times`;
    this.o.store.append({
      project_id: projectId,
      agent_id: null,
      type: 'system.notice',
      payload: {
        level: 'warning',
        code: WAKES_PAUSED,
        message: `${what} in the last hour, so automatic wakes are paused. Their messages are kept. Write to any agent of this project, or press Resume, to continue.`,
      },
    });
  }

  /**
   * Ends the project's pause (design spec §5.4): clears both windows and wakes every live agent of the project, Desk
   * first, so each decides what its held items call for. Nothing happens when the project is not paused: outside a
   * pause, the user's messages leave the windows rolling.
   */
  private resume(projectId: string): void {
    if (!this.paused.delete(projectId)) return;
    this.wakeWindows.delete(projectId);
    const agents = listAgents(this.o.store.db, projectId)
      .filter((a) => !a.archived_at)
      .sort((a, b) => Number(b.role === 'desk') - Number(a.role === 'desk') || a.created_at.localeCompare(b.created_at));
    for (const a of agents) this.wake(a.id);
  }

  /**
   * What wakeDecision reads about an agent: its row, its project, its pending approvals, its pending inbox (the user's
   * Asks as `user_question`) and the open questions to it.
   */
  private wakeState(agent: AgentRow): WakeState {
    const { store } = this.o;
    const roles = new Map(listAgents(store.db, agent.project_id).map((a) => [a.id, a.role]));
    const pending = pendingInbox(store, agent.id).flatMap((e): Item[] => {
      if (e.type === 'message.user') return [{ id: e.id, from: 'user', kind: e.payload.question ? 'user_question' : 'user' }];
      if (e.type === 'message.agent') return [{ id: e.id, from: roles.get(e.payload.from_agent_id) === 'desk' ? 'desk' : 'thread', kind: e.payload.kind }];
      return [];
    });
    // A run stopped while running is cancelled from the stop, not from the `cancelled` its ending appended.
    const cancelledAt = agent.status === 'cancelled' ? (this.stoppedAt.get(agent.id) ?? lastEvent(store.db, agent.id, 'agent.status_changed')?.id) : undefined;
    return {
      agent: { role: agent.role, status: agent.status, archived: Boolean(agent.archived_at) || this.archiving.has(agent.id) },
      ...(cancelledAt !== undefined ? { cancelledAt } : {}),
      projectArchived: Boolean(getProject(store.db, agent.project_id)?.archived_at),
      pendingApprovals: pendingApprovalsFor(store.db, agent.id).length + (this.resolving.get(agent.id) ?? 0),
      pending,
      // Open tracked questions to it, read (at or below its cursor) or not.
      open: openTo(this.messages(agent.project_id), agent.id).map((q) => ({ id: q.id, seen: q.id <= agent.inbox_cursor })),
    };
  }

  /** Queues the agent's next run, recording `queued` unless it already is. */
  private schedule(agent: AgentRow): void {
    if (agent.status !== 'queued') this.o.store.append({ project_id: agent.project_id, agent_id: agent.id, type: 'agent.status_changed', payload: { status: 'queued' } });
    // While shutting down, `queued` is recorded so the next daemon's recover() runs it.
    if (!this.shuttingDown) this.scheduler.enqueue({ agentId: agent.id, projectId: agent.project_id, model: agent.model, role: agent.role, kind: 'run' });
  }

  /**
   * Runs one job. An answer job first checks that wakeDecision still picks its question: when the question was
   * answered or withdrawn, or a revision or a user message now comes first, it returns without appending anything
   * and afterRun decides again (§3.4). It then runs in answer mode (§4): the same tools, system prompt and effort as a
   * full run, at most ANSWER_MAX_STEPS steps, no status change, and an ending that closes the question. Nothing is
   * awaited between that check and runAgent's first append, so the run starts on what the check saw, never on a
   * decision that an archive or a stop made while the job waited has changed.
   */
  private async execute(job: Job, signal: AbortSignal): Promise<void> {
    const sandboxAvailable = await this.sandboxAvailable();
    const agent = this.requireAgent(job.agentId);
    let answer: RunDeps['answer'];
    let asker: AgentRow | 'user' = 'user';
    if (job.kind === 'answer') {
      const d = wakeDecision(this.wakeState(agent));
      if (d.kind !== 'answer' || d.question !== job.answering) return;
      const q = messageById(this.messages(agent.project_id), d.question)!;
      if (q.from !== 'user') asker = this.requireAgent(q.from);
      answer = {
        question: q.id,
        asker: q.from,
        // The question itself if it is still pending (with anything older), else nothing new: never below the cursor.
        upTo: Math.max(agent.inbox_cursor, q.id),
        ending: (text, auto) => this.answerEnding(agent, q, text, auto),
      };
    }
    const maxSteps = this.o.maxSteps ?? { desk: 60, thread: 200 };
    const gate: RunDeps['gate'] = (tool, input, project, a) =>
      evaluatePolicy(tool, input, project.settings.policy, { sandboxAvailable, ...(a.git_branch ? { gitBranch: a.git_branch } : {}) });
    await runAgent(
      {
        store: this.o.store,
        adapter: this.o.adapter,
        tools: this.toolsFor(agent),
        systemPrompt: (a, p) => this.systemPrompt(a, p),
        maxSteps: answer ? ANSWER_MAX_STEPS : agent.role === 'desk' ? maxSteps.desk : maxSteps.thread,
        ...(this.o.retry ? { retry: this.o.retry } : {}),
        ...(this.proxy ? { proxy: this.proxy } : {}),
        contextWindow: (model) => (this.o.models.has(model) ? this.o.models.get(model).context_window : undefined),
        images: {
          load: (images) => this.attachments.dataUrls(images),
          vision: (model) => this.modelSeesImages(model),
          maxBytes: (model) => this.imageBudgets.get(model),
          lowerMaxBytes: (model, bytes) => this.imageBudgets.set(model, Math.min(bytes, this.imageBudgets.get(model) ?? Infinity)),
        },
        reasoningEffort: (a, project, model) =>
          effortFor(
            this.o.models.has(model) ? this.o.models.get(model) : undefined,
            a.role === 'desk' ? project.settings.desk_reasoning_effort : (a.reasoning_effort ?? project.settings.thread_reasoning_effort),
          ),
        // An answer run only reads, and sends nothing but its answer (§4.4).
        gate: answer ? answerGate(gate, asker, () => listAgents(this.o.store.db, agent.project_id)) : gate,
        toolContext: (a, runId, toolCallId, sig) =>
          buildToolContext(a, runId, toolCallId, sig, {
            sandboxEnabled: sandboxAvailable,
            jobs: this.jobs,
            services: this.services,
            readRoots: (x) => this.readRoots(x),
            writeRoots: (x) => this.writeRoots(x),
            guard: this.guard,
            gitDirs: (x) => this.gitDirs(x),
          }),
        ...(answer ? { answer } : {}),
      },
      agent.id,
      signal,
    );
  }

  /**
   * After every job: kills a finished agent's jobs; after a full run tells its Desk, after an answer run wakes the
   * asker (the answer or closure was appended with the run's end, not delivered); reminds Desk about What's up; then
   * decides what the agent does next (§3.4). An answer run never reports, so a done thread never sends a second
   * `completed`.
   */
  private afterRun(job: Job): void {
    try {
      const agent = this.requireAgent(job.agentId);
      if (TERMINAL.has(agent.status)) this.jobs.killAll(agent.id);
      if (job.kind === 'run') {
        this.notifyParent(agent, true);
      } else if (job.answering !== undefined) {
        const asker = messageById(this.messages(agent.project_id), job.answering)?.from;
        if (asker && asker !== 'user') this.wake(asker);
      }
      // An entry left by a stop whose run did not end cancelled (a shutdown raced it) must not silence a later stop.
      this.silentStops.delete(agent.id);
      this.remindWhatsUp(agent);
      this.wake(agent.id);
    } finally {
      // The stop point served this wake; from here on the `cancelled` event marks the stop.
      this.stoppedAt.delete(job.agentId);
    }
  }

  /** Desk ended its turn after changing things without rewriting What's up: remind it once (it runs again to do so). */
  private remindWhatsUp(agent: AgentRow): void {
    if (agent.role !== 'desk' || agent.status !== 'idle' || this.o.whatsUpReminder === false) return;
    const fin = lastEvent(this.o.store.db, agent.id, 'run.finished');
    if (fin?.type !== 'run.finished' || fin.payload.reason !== 'no_tool_calls' || !whatsUpStale(this.o.store, agent.id)) return;
    this.o.store.append({
      project_id: agent.project_id,
      agent_id: agent.id,
      type: 'message.agent',
      payload: { from_agent_id: agent.id, from_label: REMINDER_LABEL, kind: 'reminder', text: WHATS_UP_REMINDER },
    });
  }

  /**
   * Tells a thread's Desk about the outcome of its latest run (`ended`: that run has just ended) or of a stop. Every
   * notice opens with what the user wrote to the thread since its last report.
   */
  private notifyParent(agent: AgentRow, ended = false): void {
    if (agent.role !== 'thread' || !agent.parent_id) return;
    const send = (kind: AgentMessageKind, text: string) => this.deliver(agent.id, agent.parent_id!, kind, this.userWroteLine(agent, ended) + text);
    const finished = lastEvent(this.o.store.db, agent.id, 'run.finished');
    const fin = finished?.type === 'run.finished' ? finished.payload : undefined;
    switch (agent.status) {
      case 'done': {
        const artifacts = agent.result_artifacts ?? [];
        const result = lastEvent(this.o.store.db, agent.id, 'agent.result');
        const drafts = (result?.type === 'agent.result' && result.payload.skill_drafts) || [];
        // Messages that raced the completion stay pending (a done thread is not reopened for them): Desk decides.
        const unread = pendingInbox(this.o.store, agent.id).map((e) => `#${e.id}`);
        send(
          'completed',
          [
            // The thread's own words go in as one quoted line each (snippet); the runtime's text stays plain.
            `Summary: ${agent.result_summary ? snippet(agent.result_summary, 4000) : '(none)'}`,
            ...(artifacts.length ? [`Artifacts: ${artifacts.join(', ')}`] : []),
            ...(drafts.length ? [`Skill drafts to review and install (skill_write from_dir): ${drafts.join(', ')}`] : []),
            ...(unread.length ? [`Unread messages that arrived after it finished: ${unread.join(', ')}`] : []),
          ].join('\n'),
        );
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
                `Approval ${ap.id} needed for ${ap.tool}(${snippet(ap.arguments, 300)}): ${ap.reason}. ${
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
          send('update', `Ended its turn without completing: ${content ? snippet(content, 1000) : '(no text)'}`);
        }
        return;
      }
      default:
        return;
    }
  }

  /**
   * `(The user wrote to it since its last report: "…"[ and N more].)` and a newline, or '' when the user did not write
   * to the thread after its previous run finished (the run before the one that just ended, or the latest run when
   * the notice is about a stop outside a run).
   */
  private userWroteLine(agent: AgentRow, ended: boolean): string {
    const { store } = this.o;
    // Answer runs report nothing: "its last report" is the end of the previous full run.
    const starts = store.list({ agentId: agent.id, types: ['run.started'] });
    const answerRuns = new Set(starts.flatMap((e) => (e.type === 'run.started' && e.payload.answering !== undefined ? [e.payload.run_id] : [])));
    const full = (e: StoredEvent) => (e.type === 'run.started' || e.type === 'run.finished') && !answerRuns.has(e.payload.run_id);
    const current = ended ? starts.filter(full).at(-1)?.id : undefined;
    const previous = store
      .list({ agentId: agent.id, types: ['run.finished'] })
      .filter((e) => full(e) && (current === undefined || e.id < current))
      .at(-1);
    // An Ask an answer run answered left the thread's work as it was: not named (design spec §3.6). An Ask a full run
    // read as a message (it resumed a stopped thread, or reached a running or waiting one) is named like any other.
    const answered = new Set(starts.flatMap((e) => (e.type === 'run.started' && e.payload.answering !== undefined ? [e.payload.answering] : [])));
    const texts = store
      .list({ agentId: agent.id, after: previous?.id ?? 0, types: ['message.user'] })
      .flatMap((e) => (e.type === 'message.user' && !answered.has(e.id) ? [e.payload.text] : []));
    if (!texts.length) return '';
    return `(The user wrote to it since its last report: ${snippet(texts[0]!, 100)}${texts.length > 1 ? ` and ${texts.length - 1} more` : ''}.)\n`;
  }
}
