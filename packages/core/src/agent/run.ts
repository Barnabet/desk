import type { AgentStatus, EventInput, RunFinishReason } from '@desk/protocol';
import type { EventStore } from '../events/store';
import { newId } from '../ids';
import { classifyModelError } from '../model/errors';
import { withRetry, type RetryOptions } from '../model/retry';
import type { ChatMessage, CompletionResult, ModelAdapter } from '../model/types';
import { getAgent, getProject, type AgentRow, type ProjectRow } from '../state/queries';
import { prepareToolCall, runPreparedTool, toToolSpecs } from '../tools/registry';
import type { PolicyDecision } from '../policy/evaluate';
import type { Tool, ToolContext, ToolResult } from '../tools/types';
import { drainInbox } from './inbox';
import { buildConversation } from './transcript';

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
};

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
  store.append([
    { ...base, type: 'run.started', payload: { run_id: runId, model: agent.model } },
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
    for (;;) {
      try {
        return await withRetry(
          () =>
            deps.adapter.complete(
              { model, messages, tools: specs },
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

  try {
    for (let step = 0; step < deps.maxSteps; step++) {
      if (signal.aborted) return interrupted();
      drainInbox(store, agentId, runId);

      const current = getAgent(store.db, agentId)!;
      const messages: ChatMessage[] = [
        { role: 'system', content: system },
        ...buildConversation(store.list({ agentId })),
      ];

      const result = await callModel(fallbackModel ?? current.model, messages);
      // The call may have switched to the fallback model; attribute usage to the model that answered.
      const model = fallbackModel ?? current.model;

      store.append([
        { ...base, type: 'assistant.message', payload: { run_id: runId, content: result.content, tool_calls: result.toolCalls } },
        {
          ...base,
          type: 'usage',
          payload: {
            run_id: runId,
            model,
            prompt_tokens: result.usage.prompt_tokens,
            completion_tokens: result.usage.completion_tokens,
            ...(result.usage.cached_tokens !== undefined ? { cached_tokens: result.usage.cached_tokens } : {}),
            estimated: result.usage.estimated,
          },
        },
      ]);

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
