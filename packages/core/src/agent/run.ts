import type { AgentStatus, EventInput, ReasoningEffort, RunFinishReason } from '@desk/protocol';
import type { EventStore } from '../events/store';
import { newId } from '../ids';
import { classifyModelError } from '../model/errors';
import { withRetry, type RetryOptions } from '../model/retry';
import type { ChatMessage, CompletionResult, ModelAdapter } from '../model/types';
import { getAgent, getProject, lastEvent, type AgentRow, type ProjectRow } from '../state/queries';
import { prepareToolCall, runPreparedTool, toToolSpecs } from '../tools/registry';
import type { PolicyDecision } from '../policy/evaluate';
import type { Tool, ToolContext, ToolResult } from '../tools/types';
import { drainInbox } from './inbox';
import { chooseSplit, compactionPrompt, DEFAULT_KEEP_MESSAGES, shouldCompact } from './compaction';
import { buildConversation, buildCurrentConversation } from './transcript';

export type RunDeps = {
  store: EventStore;
  adapter: ModelAdapter;
  tools: Tool[];
  systemPrompt: (agent: AgentRow, project: ProjectRow) => string;
  maxSteps: number;
  retry?: RetryOptions;
  /** Policy decision for a validated call. */
  gate: (tool: Tool, input: unknown, project: ProjectRow, agent: AgentRow) => PolicyDecision;
  toolContext: (agent: AgentRow, runId: string, toolCallId: string, signal: AbortSignal) => ToolContext;
  /** When set, `proxy_down` errors pause here until the proxy is back instead of failing the run. */
  proxy?: { markDown(projectId: string): void; waitUntilUp(signal: AbortSignal): Promise<void> };
  /**
   * The reasoning level to send for an agent's call to `model` (the agent's own level or its project's setting,
   * checked against what the model accepts); undefined sends none.
   */
  reasoningEffort?: (agent: AgentRow, project: ProjectRow, model: string) => ReasoningEffort | undefined;
  /** The model's context window in tokens; without it only a context overflow triggers compaction. */
  contextWindow?: (model: string) => number | undefined;
  compaction?: { keepMessages?: number };
};

/** Budget used to render a compaction request when the model's window is unknown. */
const DEFAULT_CONTEXT_WINDOW = 200_000;

export type RunOutcome = { reason: RunFinishReason; status: AgentStatus };

/** Abort reason used when the daemon stops: the run ends resumable (agent left `queued`) instead of cancelled. */
export const SHUTDOWN_REASON = 'daemon_shutdown';

/** Runs one activation of an agent until it yields, replies without tools, hits the step limit, is aborted, or fails. Never throws. */
export async function runAgent(deps: RunDeps, agentId: string, signal: AbortSignal): Promise<RunOutcome> {
  const { store } = deps;
  const agent = getAgent(store.db, agentId);
  if (!agent) throw new Error(`Unknown agent: ${agentId}`);
  const project = getProject(store.db, agent.project_id);
  if (!project) throw new Error(`Unknown project: ${agent.project_id}`);

  const runId = newId();
  const base = { project_id: agent.project_id, agent_id: agentId };
  /** Read per call: settings can change during a run, and a fallback model may take different levels. */
  const effortFor = (model: string) => deps.reasoningEffort?.(getAgent(store.db, agentId) ?? agent, getProject(store.db, agent.project_id) ?? project, model);
  const startEffort = effortFor(agent.model);
  store.append([
    { ...base, type: 'run.started', payload: { run_id: runId, model: agent.model, ...(startEffort ? { reasoning_effort: startEffort } : {}) } },
    { ...base, type: 'agent.status_changed', payload: { status: 'running' } },
  ]);

  const finish = (reason: RunFinishReason, status: AgentStatus, detail?: string): RunOutcome => {
    store.append([
      { ...base, type: 'run.finished', payload: { run_id: runId, reason, ...(detail ? { detail } : {}) } },
      { ...base, type: 'agent.status_changed', payload: { status, ...(detail ? { reason: detail } : {}) } },
    ]);
    return { reason, status };
  };

  const interrupted = (): RunOutcome =>
    signal.reason === SHUTDOWN_REASON ? finish('error', 'queued', SHUTDOWN_REASON) : finish('stopped', 'cancelled');

  const workspace = agent.workspace_path;
  if (!workspace) return finish('error', 'failed', 'Agent has no workspace');
  const specs = toToolSpecs(deps.tools);
  // Snapshot per run: a stable prefix (prompt caching) and no mid-run surprises (e.g. an agent's own fresh
  // artifact appearing as pre-existing context). Changes made by others arrive as messages instead.
  const system = deps.systemPrompt(agent, project);

  /** Set once sustained rate limiting switched this run to the project's fallback model. */
  let fallbackModel: string | undefined;
  const callModel = async (model: string, messages: ChatMessage[]): Promise<CompletionResult> => {
    const effort = effortFor(model);
    for (;;) {
      try {
        return await withRetry(
          () =>
            deps.adapter.complete(
              { model, messages, tools: specs, ...(effort ? { reasoningEffort: effort } : {}) },
              {
                signal,
                onText: (text) =>
                  store.publishEphemeral({ type: 'assistant.delta', project_id: agent.project_id, agent_id: agentId, payload: { run_id: runId, text } }),
              },
            ),
          { ...deps.retry, signal },
        );
      } catch (e) {
        const err = classifyModelError(e);
        if (err.kind === 'proxy_down' && deps.proxy) {
          deps.proxy.markDown(agent.project_id);
          await deps.proxy.waitUntilUp(signal);
          continue;
        }
        const fallback = getProject(store.db, agent.project_id)?.settings.fallback_model;
        if (err.kind === 'rate_limited' && !fallbackModel && fallback && fallback !== model) {
          fallbackModel = fallback;
          store.append({ ...base, type: 'agent.model_switched', payload: { from: model, to: fallback, reason: err.message, scope: 'run' } });
          return callModel(fallback, messages);
        }
        throw err;
      }
    }
  };

  const windowOf = (model: string) => deps.contextWindow?.(model);
  const recordUsage = (model: string, usage: CompletionResult['usage']): EventInput => ({
    ...base,
    type: 'usage',
    payload: {
      run_id: runId,
      model,
      prompt_tokens: usage.prompt_tokens,
      completion_tokens: usage.completion_tokens,
      ...(usage.cached_tokens !== undefined ? { cached_tokens: usage.cached_tokens } : {}),
      estimated: usage.estimated,
    },
  });

  /**
   * Summarises all but the most recent messages into a checkpoint (originals stay in the event log).
   * Returns false when there is nothing to compact or the summary call failed; the conversation is then untouched.
   */
  const compact = async (model: string, trigger: 'threshold' | 'overflow'): Promise<boolean> => {
    const conversation = buildCurrentConversation(store.list({ agentId }));
    const keep = deps.compaction?.keepMessages ?? DEFAULT_KEEP_MESSAGES;
    const split = chooseSplit(conversation, keep) ?? (trigger === 'overflow' ? chooseSplit(conversation, 1) : null);
    if (!split) return false;
    const covered = conversation.slice(0, split.index).map((m) => m.message);
    try {
      const result = await withRetry(
        () => deps.adapter.complete({ model, messages: compactionPrompt(covered, windowOf(model) ?? DEFAULT_CONTEXT_WINDOW), tools: [] }, { signal }),
        { ...deps.retry, signal },
      );
      const checkpoint = result.content?.trim();
      if (!checkpoint) return false;
      store.append([
        { ...base, type: 'context.compacted', payload: { run_id: runId, checkpoint, up_to: split.upTo, trigger } },
        recordUsage(model, result.usage),
      ]);
      return true;
    } catch (e) {
      if (classifyModelError(e).kind === 'aborted' || signal.aborted) throw e;
      return false;
    }
  };

  // A conversation that ended the previous run above the threshold is compacted before its first call.
  const lastMeasure = lastEvent(store.db, agentId, 'usage');
  const lastCheckpoint = lastEvent(store.db, agentId, 'context.compacted');
  let compactNext =
    lastMeasure?.type === 'usage' &&
    (!lastCheckpoint || lastCheckpoint.id < lastMeasure.id) &&
    shouldCompact(lastMeasure.payload.prompt_tokens, windowOf(lastMeasure.payload.model) ?? Infinity);
  /** A failed threshold compaction is not retried every step. */
  let thresholdCompactionFailed = false;

  try {
    for (let step = 0; step < deps.maxSteps; step++) {
      if (signal.aborted) return interrupted();
      drainInbox(store, agentId, runId);

      const current = getAgent(store.db, agentId)!;
      if (compactNext && !thresholdCompactionFailed) {
        if (!(await compact(fallbackModel ?? current.model, 'threshold'))) thresholdCompactionFailed = true;
      }
      const conversation = (): ChatMessage[] => [{ role: 'system', content: system }, ...buildConversation(store.list({ agentId }))];

      let result: CompletionResult;
      try {
        result = await callModel(fallbackModel ?? current.model, conversation());
      } catch (e) {
        const err = classifyModelError(e);
        // Forced compaction, once per call; a second overflow fails the run.
        if (err.kind !== 'context_overflow' || !(await compact(fallbackModel ?? current.model, 'overflow'))) throw err;
        result = await callModel(fallbackModel ?? current.model, conversation());
      }
      // The call may have switched to the fallback model; attribute usage to the model that answered.
      const model = fallbackModel ?? current.model;

      store.append([
        { ...base, type: 'assistant.message', payload: { run_id: runId, content: result.content, tool_calls: result.toolCalls } },
        recordUsage(model, result.usage),
      ]);
      compactNext = shouldCompact(result.usage.prompt_tokens, windowOf(model) ?? Infinity);

      if (result.toolCalls.length === 0) return finish('no_tool_calls', 'idle');

      store.append(
        result.toolCalls.map(
          (tc): EventInput => ({ ...base, type: 'tool.call', payload: { run_id: runId, tool_call_id: tc.id, name: tc.name, arguments: tc.arguments } }),
        ),
      );
      const freshProject = getProject(store.db, agent.project_id) ?? project;
      type Outcome = { result: ToolResult; pending?: undefined } | { result?: undefined; pending: PolicyDecision };
      const outcomes: Outcome[] = await Promise.all(
        result.toolCalls.map(async (tc): Promise<Outcome> => {
          const prepared = prepareToolCall(deps.tools, tc);
          if (!prepared.ok) return { result: prepared.result };
          const decision = deps.gate(prepared.tool, prepared.input, freshProject, current);
          if (decision.action === 'deny') return { result: { status: 'denied', content: `Denied by policy. ${decision.reason}` } };
          if (decision.action === 'ask') return { pending: decision };
          return { result: await runPreparedTool(prepared.tool, prepared.input, deps.toolContext(current, runId, tc.id, signal)) };
        }),
      );
      const done = result.toolCalls.flatMap((tc, i) => (outcomes[i]!.result ? [{ tc, r: outcomes[i]!.result! }] : []));
      store.append(
        done.map(
          ({ tc, r }): EventInput => ({
            ...base,
            type: 'tool.result',
            payload: { run_id: runId, tool_call_id: tc.id, name: tc.name, status: r.status, content: r.content },
          }),
        ),
      );
      const pending = result.toolCalls.flatMap((tc, i) => (outcomes[i]!.pending ? [{ tc, d: outcomes[i]!.pending! }] : []));
      if (pending.length) {
        store.append(
          pending.map(
            ({ tc, d }): EventInput => ({
              ...base,
              type: 'approval.requested',
              payload: {
                approval_id: newId(),
                run_id: runId,
                tool_call_id: tc.id,
                tool: tc.name,
                arguments: tc.arguments,
                reason: d.reason,
                delegate_to_desk: d.delegateToDesk,
              },
            }),
          ),
        );
        return finish('yielded', 'waiting', `Awaiting approval: ${pending.map((p) => p.tc.name).join(', ')}`);
      }
      const results = done.map((d) => d.r);

      const yielded = results.find((r) => r.yield)?.yield;
      if (yielded) return finish('yielded', yielded.status, yielded.reason);
    }
    return finish('max_steps', 'idle', `Reached the limit of ${deps.maxSteps} steps`);
  } catch (e) {
    const err = classifyModelError(e);
    if (err.kind === 'aborted' || signal.aborted) return interrupted();
    return finish('error', 'failed', err.message);
  }
}
