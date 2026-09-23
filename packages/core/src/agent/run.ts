import type { AgentStatus, EventInput, RunFinishReason } from '@desk/protocol';
import type { EventStore } from '../events/store';
import { newId } from '../ids';
import { classifyModelError } from '../model/errors';
import { withRetry, type RetryOptions } from '../model/retry';
import type { ChatMessage, ModelAdapter } from '../model/types';
import { getAgent, getProject, type AgentRow, type ProjectRow } from '../state/queries';
import { executeToolCall, toToolSpecs } from '../tools/registry';
import { NO_SANDBOX } from '../tools/sandbox';
import type { Tool } from '../tools/types';
import { drainInbox } from './inbox';
import { buildConversation } from './transcript';

export type RunDeps = {
  store: EventStore;
  adapter: ModelAdapter;
  tools: Tool[];
  systemPrompt: (agent: AgentRow, project: ProjectRow) => string;
  maxSteps: number;
  retry?: RetryOptions;
};

export type RunOutcome = { reason: RunFinishReason; status: AgentStatus };

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

  const workspace = agent.workspace_path;
  if (!workspace) return finish('error', 'failed', 'Agent has no workspace');
  const specs = toToolSpecs(deps.tools);

  try {
    for (let step = 0; step < deps.maxSteps; step++) {
      if (signal.aborted) return finish('stopped', 'cancelled');
      drainInbox(store, agentId, runId);

      const current = getAgent(store.db, agentId)!;
      const messages: ChatMessage[] = [
        { role: 'system', content: deps.systemPrompt(current, project) },
        ...buildConversation(store.list({ agentId })),
      ];

      const result = await withRetry(
        () =>
          deps.adapter.complete(
            { model: current.model, messages, tools: specs },
            {
              signal,
              onText: (text) =>
                store.publishEphemeral({ type: 'assistant.delta', project_id: agent.project_id, agent_id: agentId, payload: { run_id: runId, text } }),
            },
          ),
        { ...deps.retry, signal },
      );

      store.append([
        { ...base, type: 'assistant.message', payload: { run_id: runId, content: result.content, tool_calls: result.toolCalls } },
        {
          ...base,
          type: 'usage',
          payload: {
            run_id: runId,
            model: current.model,
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
      const results = await Promise.all(
        result.toolCalls.map((tc) =>
          executeToolCall(deps.tools, tc, {
            projectId: agent.project_id,
            agentId,
            runId,
            toolCallId: tc.id,
            workspace,
            readRoots: [workspace],
            signal,
            sandbox: NO_SANDBOX,
          }),
        ),
      );
      store.append(
        result.toolCalls.map(
          (tc, i): EventInput => ({
            ...base,
            type: 'tool.result',
            payload: { run_id: runId, tool_call_id: tc.id, name: tc.name, status: results[i]!.status, content: results[i]!.content },
          }),
        ),
      );

      const yielded = results.find((r) => r.yield)?.yield;
      if (yielded) return finish('yielded', yielded.status, yielded.reason);
    }
    return finish('max_steps', 'idle', `Reached the limit of ${deps.maxSteps} steps`);
  } catch (e) {
    const err = classifyModelError(e);
    if (err.kind === 'aborted') return finish('stopped', 'cancelled');
    return finish('error', 'failed', err.message);
  }
}
