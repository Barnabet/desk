import type { AgentStatus, EventInput, ReasoningEffort, RunFinishReason, ToolImage } from '@desk/protocol';
import type { EventStore } from '../events/store';
import { newId } from '../ids';
import { classifyModelError, imageRefusal, type ModelError } from '../model/errors';
import { withRetry, type RetryOptions } from '../model/retry';
import type { ChatMessage, CompletionResult, ModelAdapter } from '../model/types';
import { getAgent, getProject, lastEvent, type AgentRow, type ProjectRow } from '../state/queries';
import { prepareToolCall, runPreparedTool, toToolSpecs } from '../tools/registry';
import type { PolicyDecision } from '../policy/evaluate';
import type { Tool, ToolContext, ToolResult } from '../tools/types';
import { WHY } from './answer';
import { drainEvent, drainInbox } from './inbox';
import { chooseSplit, compactionPrompt, DEFAULT_KEEP_MESSAGES, shouldCompact } from './compaction';
import { buildCurrentConversation, imagesInWindow, occurrenceOf, pixelGroups, showImages, withholdMore, type ConversationImage, type Withheld } from './transcript';

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
  /** Stored images (view_image) as pixels; without it, or for a model without vision, images are text references. */
  images?: {
    /** Data URLs of stored images by sha256; missing attachments are left out. */
    load(images: ToolImage[]): Promise<Map<string, string>>;
    vision(model: string): boolean;
    /** Bytes of images a request to the model may carry, together; lowered after the endpoint refuses a request as too large. */
    maxBytes?(model: string): number | undefined;
    lowerMaxBytes?(model: string, bytes: number): void;
  };
  /**
   * Answer mode (design spec §4): the run only answers `question` from `asker` (an agent id, or 'user'). It never
   * changes the agent's status. Its start drains the inbox up to `upTo` in the same append as `run.started`, later
   * steps do not drain, and every ending appends `ending(…)` together with `run.finished`.
   */
  answer?: {
    question: number;
    asker: string;
    upTo: number;
    /**
     * The events that close the question, appended with `run.finished`: the answer for the reply `text`, or with
     * `auto` the runtime's closure `(<title> <text>.)`. None for the user's question, or once the question has an
     * answer.
     */
    ending(text: string, auto?: boolean): EventInput[];
  };
};

/** A request's messages, and the images it sends as pixels (one group per images message, the most recent first). */
type Built = { messages: ChatMessage[]; pixels: ConversationImage[][] };

/**
 * How a request's images change while retrying after the endpoint refused it because of them. Nothing is recorded
 * until a retry succeeds, so a refusal that was not about the images after all leaves no trace.
 */
type ImageTrial = {
  /** Groups of images sent as text, each blamed by one refusal: at least one image of each is refused. */
  withheld: Array<{ images: ConversationImage[]; reason: string }>;
  /** A lower byte budget for pixels, after a request was refused as too large. */
  maxBytes?: number;
  /**
   * Finding the images an endpoint refuses, from the request first refused: its newest group, then the older ones.
   * Step 1 withholds the newest, step 2 only the older, step 3 both; whichever succeeds names the guilty groups
   * (a step that failed with some group shown means that group holds a refused image).
   */
  search?: { newest: ConversationImage[]; older: ConversationImage[]; step: 1 | 2 | 3; reasons: string[] };
};

/** The images a trial sends as text, by occurrence, as images.withheld will record them. */
function trialWithheld(trial: ImageTrial | undefined): Map<string, Withheld> {
  const out = new Map<string, Withheld>();
  for (const g of trial?.withheld ?? []) for (const c of g.images) out.set(occurrenceOf(c), { reason: g.reason, count: g.images.length });
  return out;
}

/** The next change to try after the endpoint refused `built` because of its images; null when nothing is left to try. */
function nextImageTrial(err: ModelError, built: Built, trial: ImageTrial | undefined): ImageTrial | null {
  const refusal = imageRefusal(err);
  if (!refusal) return null;
  if (refusal === 'too_large') {
    const sent = built.pixels.flat().reduce((n, c) => n + c.image.bytes, 0);
    return sent ? { ...(trial ?? { withheld: [] }), maxBytes: Math.floor(sent / 2) } : null;
  }
  const s = trial?.search;
  let search: NonNullable<ImageTrial['search']>;
  if (!s) {
    const [newest, ...older] = built.pixels;
    if (!newest) return null;
    search = { newest, older: older.flat(), step: 1, reasons: [err.message] };
  } else if (s.step === 1 && s.older.length) search = { ...s, step: 2, reasons: [...s.reasons, err.message] };
  else if (s.step === 2) search = { ...s, step: 3, reasons: [...s.reasons, err.message] };
  else return null;
  const [first, second, third] = search.reasons as [string, string?, string?];
  const withheld =
    search.step === 1
      ? [{ images: search.newest, reason: first }]
      : search.step === 2
        ? [{ images: search.older, reason: second! }]
        : [
            { images: search.newest, reason: third! },
            { images: search.older, reason: second! },
          ];
  return { ...(trial?.maxBytes !== undefined ? { maxBytes: trial.maxBytes } : {}), withheld, search };
}

/** Budget used to render a compaction request when the model's window is unknown. */
const DEFAULT_CONTEXT_WINDOW = 200_000;

export type RunOutcome = {
  reason: RunFinishReason;
  /** The agent's status once the run ended (an answer run leaves it unchanged). */
  status: AgentStatus;
  /** An answer run's text reply. */
  text?: string;
};

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
  /** Answer mode (design spec §4): this run only answers one question. */
  const answering = deps.answer;
  const started: EventInput = {
    ...base,
    type: 'run.started',
    payload: {
      run_id: runId,
      model: agent.model,
      ...(startEffort ? { reasoning_effort: startEffort } : {}),
      ...(answering ? { answering: answering.question } : {}),
    },
  };
  // An answer run starts with its only drain, in one append and before any early exit, so its question is read by this
  // run and no other; it never shows as running (§4.3). `drainEvent` is null only for an unknown agent, and this one
  // was loaded above.
  const opening: EventInput = answering
    ? drainEvent(store, agentId, runId, answering.upTo)!
    : { ...base, type: 'agent.status_changed', payload: { status: 'running' } };
  store.append([started, opening]);

  /**
   * Ends the run: `run.finished` and the agent's new status. An answer run keeps its status: `end` (its answer or
   * closure) goes with `run.finished` instead, after `before` (its text reply), all in one append (§4.5, §4.6).
   */
  const finish = (reason: RunFinishReason, status: AgentStatus, detail?: string, end: EventInput[] = [], before: EventInput[] = []): RunOutcome => {
    const events: EventInput[] = [...before, { ...base, type: 'run.finished', payload: { run_id: runId, reason, ...(detail ? { detail } : {}) } }];
    if (answering) events.push(...end);
    else events.push({ ...base, type: 'agent.status_changed', payload: { status, ...(detail ? { reason: detail } : {}) } });
    store.append(events);
    return { reason, status: answering ? (getAgent(store.db, agentId)?.status ?? agent.status) : status };
  };

  /** An answer run's closure `(<title> <why>.)`: none outside answer mode, for the user's question, or once answered. */
  const closing = (why: string): EventInput[] => answering?.ending(why, true) ?? [];

  /** Whether a message tool already recorded the answer to an agent's question: the message path (§4.5). */
  const answered = (): boolean =>
    answering !== undefined &&
    answering.asker !== 'user' &&
    store
      .list({ agentId: answering.asker, after: answering.question, types: ['message.agent'] })
      .some((e) => e.type === 'message.agent' && e.payload.reply_to === answering.question);

  const interrupted = (): RunOutcome =>
    signal.reason === SHUTDOWN_REASON
      ? finish('error', 'queued', SHUTDOWN_REASON, closing(WHY.restart))
      : finish('stopped', 'cancelled', undefined, closing(WHY.stopped));
  /** The user or Desk stopped this run (a daemon shutdown is not a stop: it keeps the run's yield). */
  const stopped = () => signal.aborted && signal.reason !== SHUTDOWN_REASON;
  /** The result of a call that a stop kept from running or from asking for approval. */
  const deniedByStop = (tc: { id: string; name: string }): EventInput => ({
    ...base,
    type: 'tool.result',
    payload: { run_id: runId, tool_call_id: tc.id, name: tc.name, status: 'denied', content: 'Denied: the agent was stopped' },
  });

  const workspace = agent.workspace_path;
  if (!workspace) return finish('error', 'failed', 'Agent has no workspace', closing(WHY.noWorkspace));
  const specs = toToolSpecs(deps.tools);
  // Snapshot per run: a stable prefix (prompt caching) and no mid-run surprises (e.g. an agent's own fresh
  // artifact appearing as pre-existing context). Changes made by others arrive as messages instead.
  const system = deps.systemPrompt(agent, project);

  /** Set once sustained rate limiting switched this run to the project's fallback model. */
  let fallbackModel: string | undefined;

  /** Records what made a refused request go through: the images sent as text from now on, and a lower byte budget. */
  const commitImageTrial = (model: string, trial: ImageTrial) => {
    if (trial.withheld.length) {
      store.append(
        trial.withheld.map(
          (g): EventInput => ({
            ...base,
            type: 'images.withheld',
            payload: { run_id: runId, reason: g.reason, images: g.images.map((c) => ({ tool_call_id: c.toolCallId, sha256: c.image.sha256, name: c.image.name })) },
          }),
        ),
      );
    }
    if (trial.maxBytes !== undefined) deps.images?.lowerMaxBytes?.(model, trial.maxBytes);
  };

  /** `build` makes the messages for the model that answers (the fallback may not take images). */
  const callModel = async (model: string, build: (model: string, trial?: ImageTrial) => Promise<Built>): Promise<CompletionResult> => {
    const effort = effortFor(model);
    let trial: ImageTrial | undefined;
    let built = await build(model);
    for (;;) {
      try {
        const result = await withRetry(
          () =>
            deps.adapter.complete(
              { model, messages: built.messages, tools: specs, ...(effort ? { reasoningEffort: effort } : {}) },
              {
                signal,
                onText: (text) =>
                  store.publishEphemeral({ type: 'assistant.delta', project_id: agent.project_id, agent_id: agentId, payload: { run_id: runId, text } }),
              },
            ),
          { ...deps.retry, signal },
        );
        if (trial) commitImageTrial(model, trial);
        return result;
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
          return callModel(fallback, build);
        }
        // An image the endpoint cannot take would otherwise fail every later call the same way.
        const next = deps.images ? nextImageTrial(err, built, trial) : null;
        if (next) {
          trial = next;
          built = await build(model, trial);
          continue;
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
      // An answer run drained once, at its start: what arrives meanwhile is decided after it (§4.3).
      if (!answering) drainInbox(store, agentId, runId);

      const current = getAgent(store.db, agentId)!;
      if (compactNext && !thresholdCompactionFailed) {
        if (!(await compact(fallbackModel ?? current.model, 'threshold'))) thresholdCompactionFailed = true;
      }
      const conversation = async (model: string, trial?: ImageTrial): Promise<Built> => {
        let tagged = buildCurrentConversation(store.list({ agentId }));
        if (deps.images?.vision(model)) {
          tagged = withholdMore(tagged, trialWithheld(trial));
          const budget = deps.images.maxBytes?.(model);
          const window = { maxBytes: trial?.maxBytes !== undefined ? Math.min(trial.maxBytes, budget ?? Infinity) : budget };
          const urls = await deps.images.load([...imagesInWindow(tagged, window)].map((c) => c.image));
          tagged = showImages(tagged, (image) => urls.get(image.sha256) ?? null, window);
        }
        return { messages: [{ role: 'system', content: system }, ...tagged.map((m) => m.message)], pixels: pixelGroups(tagged) };
      };

      let result: CompletionResult;
      try {
        result = await callModel(fallbackModel ?? current.model, conversation);
      } catch (e) {
        const err = classifyModelError(e);
        // Forced compaction, once per call; a second overflow fails the run.
        if (err.kind !== 'context_overflow' || !(await compact(fallbackModel ?? current.model, 'overflow'))) throw err;
        result = await callModel(fallbackModel ?? current.model, conversation);
      }
      // The call may have switched to the fallback model; attribute usage to the model that answered.
      const model = fallbackModel ?? current.model;

      const reply: EventInput[] = [
        { ...base, type: 'assistant.message', payload: { run_id: runId, content: result.content, tool_calls: result.toolCalls } },
        recordUsage(model, result.usage),
      ];
      compactNext = shouldCompact(result.usage.prompt_tokens, windowOf(model) ?? Infinity);

      // An answer run's text reply is its answer: the reply, run.finished and the answer are one append (§4.5).
      if (answering && result.toolCalls.length === 0 && !stopped()) {
        const text = result.content ?? '';
        return { ...finish('no_tool_calls', 'idle', undefined, answering.ending(text), reply), text };
      }
      store.append(reply);
      if (result.toolCalls.length === 0) return stopped() ? interrupted() : finish('no_tool_calls', 'idle');

      store.append(
        result.toolCalls.map(
          (tc): EventInput => ({ ...base, type: 'tool.call', payload: { run_id: runId, tool_call_id: tc.id, name: tc.name, arguments: tc.arguments } }),
        ),
      );
      // A stop that landed as the model replied runs none of the calls: each gets a denial, so every call keeps a result.
      if (stopped()) {
        store.append(result.toolCalls.map(deniedByStop));
        return interrupted();
      }
      const freshProject = getProject(store.db, agent.project_id) ?? project;
      type Outcome = { result: ToolResult; pending?: undefined } | { result?: undefined; pending: PolicyDecision };
      const outcomes: Outcome[] = await Promise.all(
        result.toolCalls.map(async (tc): Promise<Outcome> => {
          const prepared = prepareToolCall(deps.tools, tc);
          if (!prepared.ok) return { result: prepared.result };
          const decision = deps.gate(prepared.tool, prepared.input, freshProject, current);
          if (decision.action === 'deny') return { result: { status: 'denied', content: `Denied by policy. ${decision.reason}` } };
          if (decision.action === 'ask') return { pending: decision };
          // The model that asked for the call (the fallback after a switch): view_image checks that it sees images.
          return { result: await runPreparedTool(prepared.tool, prepared.input, { ...deps.toolContext(current, runId, tc.id, signal), model }) };
        }),
      );
      const done = result.toolCalls.flatMap((tc, i) => (outcomes[i]!.result ? [{ tc, r: outcomes[i]!.result! }] : []));
      store.append(
        done.map(
          ({ tc, r }): EventInput => ({
            ...base,
            type: 'tool.result',
            payload: { run_id: runId, tool_call_id: tc.id, name: tc.name, status: r.status, content: r.content, ...(r.images?.length ? { images: r.images } : {}) },
          }),
        ),
      );
      const pending = result.toolCalls.flatMap((tc, i) => (outcomes[i]!.pending ? [{ tc, d: outcomes[i]!.pending! }] : []));
      // A stop during the tool phase ends the run cancelled: no approval is requested and no yield is honoured. Calls
      // that would have asked get a denial, so every call keeps its result.
      if (stopped()) {
        store.append(pending.map(({ tc }) => deniedByStop(tc)));
        return interrupted();
      }
      // The answer went to the asker as a message and was recorded as the answer: the run ends with nothing more (§4.5).
      if (answered()) return finish('yielded', 'idle');
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
      // (An answer run cannot yield: its gate denies complete and wait_for_reply. If it did, it would still close.)
      if (yielded) return finish('yielded', yielded.status, yielded.reason, closing(WHY.unfinished));
    }
    return finish('max_steps', 'idle', `Reached the limit of ${deps.maxSteps} steps`, closing(WHY.unfinished));
  } catch (e) {
    const err = classifyModelError(e);
    if (err.kind === 'aborted' || signal.aborted) return interrupted();
    return finish('error', 'failed', err.message, closing(WHY.error(err.message)));
  }
}
