import type { z } from 'zod';
import type {
  AgentMessageKind,
  AgentStatus,
  ArtifactKind,
  MemoryKind,
  MessagesState,
  ProjectSettingsPatch,
  ReasoningEffort,
  SkillScope,
  ToolImage,
  ToolResultStatus,
} from '@desk/protocol';
import type { AttachmentStore } from '../attachments/store';
import type { SkillSaveInput, SkillStore, SkillSummary } from '../skills/store';
import type { EventStore } from '../events/store';
import type { ServiceRow } from '../state/queries';
import type { JobManager } from './jobs';
import type { SandboxSpec } from './sandbox';

export type ToolContext = {
  projectId: string;
  agentId: string;
  runId: string;
  toolCallId: string;
  workspace: string;
  readRoots: string[];
  /** Where file tools may write: the workspace, plus project sources that allow agents to write. */
  writeRoots?: string[];
  signal: AbortSignal;
  sandbox: SandboxSpec;
  jobs: JobManager;
  services: RuntimeServices;
  /** Set when the workspace is a git worktree. */
  git: { branch: string; base: string } | null;
  /** The model that asked for this call (a run's fallback model after a switch); unset outside a model step, e.g. after an approval. */
  model?: string;
};

/** A message tool's request to Runtime.send (design spec §2.4). `to` is an agent id, or a thread's exact title. */
export type SendInput = {
  from: string;
  to: string;
  kind: Extract<AgentMessageKind, 'note' | 'question' | 'revision' | 'update' | 'blocker'>;
  text: string;
  toolCallId?: string;
};

/** What Runtime.send stored: the message id, its kind (`answer` when it answered a question), and the tool's result. */
export type SendResult = { id: number; kind: AgentMessageKind; replyTo?: number; note: string };

/** Runtime capabilities available to tools (implemented by Runtime). */
export interface RuntimeServices {
  readonly store: EventStore;
  readonly skills: SkillStore;
  /** Content-addressed images shown to models (`<data>/attachments`). */
  readonly attachments: AttachmentStore;
  /** The model a tool call runs under (`model`, else the agent's own), and whether it accepts images. */
  agentModel(agentId: string, model?: string): { id: string; vision: boolean };
  activateSkills(agentId: string, names: string[]): SkillSummary[];
  saveSkill(
    input: SkillSaveInput,
    meta: { origin?: string; changeNote?: string; projectId?: string; agentId?: string },
  ): { version: number; dir: string; created: boolean; description: string };
  deleteSkill(scope: SkillScope, name: string, projectId: string | undefined, meta: { origin?: string; projectId?: string; agentId?: string }): void;
  /** Resolves a skill draft directory, which must lie in a workspace of the project. */
  skillDraftDir(projectId: string, path: string): string;
  writeMemory(projectId: string, input: { kind: MemoryKind; content: string; supersedes?: string }, source: string): string;
  libraryDir(projectId: string): string;
  publishToLibrary(
    projectId: string,
    file: string,
    meta: { title: string; kind: ArtifactKind; description: string; name?: string },
    origin: string,
  ): Promise<{ id: string; path: string }>;
  /**
   * Stores a message on the recipient's stream and wakes it if it should run; returns the message id. `opts` records
   * the question it answers, a runtime closure, a tracked question, or the tool call that sent it.
   */
  deliver(
    fromAgentId: string,
    toAgentId: string,
    kind: AgentMessageKind,
    text: string,
    opts?: { replyTo?: number; auto?: boolean; tracked?: boolean; toolCallId?: string },
  ): number;
  /**
   * The message tools' one send path: refuses what the recipient cannot take (the Error's message is the tool result),
   * records an answer or delivers, and says what happens next.
   */
  send(input: SendInput): SendResult;
  /** The project's message fold: the agent directory, every message with its question state, the answer runs. */
  messages(projectId: string): MessagesState;
  spawnThread(parentId: string, input: { title: string; brief: string; gitSourceId?: string; model?: string; reasoningEffort?: ReasoningEffort; skills?: string[] }): Promise<string>;
  /** Whether the pause of the project's automatic wakes is what keeps the agent from running now (design spec §5.4). */
  heldByPause(agentId: string): boolean;
  stopAgent(agentId: string, opts?: { by?: string; reason?: string }): void;
  /** Whether the agent's running job was stopped and is still winding down (its run ends cancelled). */
  isStopping(agentId: string): boolean;
  resolveApproval(approvalId: string, decision: 'approved' | 'denied', opts?: { by?: 'user' | 'desk'; note?: string }): Promise<void>;
  updateSettings(projectId: string, patch: ProjectSettingsPatch): void;
  /**
   * PATH entries and variables from Desk-managed skill runtimes: of one skill (`only`), or of every active skill of the
   * agent whose runtime is ready. `blocked` explains why `only`'s runtime cannot be used yet.
   */
  skillEnv(agentId: string, only?: { scope: SkillScope; name: string }): { bins: string[]; vars: Record<string, string>; blocked: string | null; note: string | null };
  /** Before a skill run: builds a built-in skill's environment on first use and waits for it (up to 5 minutes). */
  prepareSkillRuntime(agentId: string, skill: { scope: SkillScope; name: string }, signal?: AbortSignal): Promise<{ waitedMs: number }>;
  /** Starts (or restarts, under an existing name) a project service in a thread's workspace. `by` = `user` or `agent:<id>`. */
  startService(projectId: string, input: { name: string; command: string; cwd?: string; threadId?: string; sourceId?: string; by: string }): Promise<ServiceRow>;
  stopService(serviceId: string, by: string): Promise<ServiceRow>;
  restartService(serviceId: string, by: string): Promise<ServiceRow>;
  serviceLogs(serviceId: string, lines: number): { text: string; truncated: boolean };
}

export type ToolYield = { status: AgentStatus; reason?: string };
/** `images` are stored attachments shown to the model after the result (view_image). */
export type ToolOutput = string | { content: string; yield?: ToolYield; images?: ToolImage[] };
export type ToolResult = { status: ToolResultStatus; content: string; yield?: ToolYield; images?: ToolImage[] };

export type PolicySubject = { branch?: string; command?: string; domain?: string };

/** Declares that a tool is checked against project policy rules before it runs. */
/** Facts about the calling agent that policy subjects may depend on. */
export type GateContext = { gitBranch?: string };

export type ToolGate<I = any> = {
  subject: (input: I, gctx: GateContext) => PolicySubject;
  /** Decision when no rule matches: `ask` for outward-facing tools, `auto` for sandboxed shell. */
  unmatched: 'ask' | 'auto';
  /** Rules written for these tools apply too (a new tool inherits saved policies, e.g. service_start ← bash_background). */
  alsoMatches?: string[];
};

export type Tool<I = any> = {
  name: string;
  description: string;
  input: z.ZodType<I>;
  gate?: ToolGate<I>;
  execute(input: I, ctx: ToolContext): Promise<ToolOutput>;
};

export function defineTool<S extends z.ZodType>(def: {
  name: string;
  description: string;
  input: S;
  gate?: ToolGate<z.output<S>>;
  execute(input: z.output<S>, ctx: ToolContext): Promise<ToolOutput>;
}): Tool<z.output<S>> {
  return def as Tool<z.output<S>>;
}

/** Thrown by tools when an action is not permitted; surfaces as tool.result status "denied". */
export class ToolDenied extends Error {
  constructor(message: string) {
    super(message);
    this.name = 'ToolDenied';
  }
}
