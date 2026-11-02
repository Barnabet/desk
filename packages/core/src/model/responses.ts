import type { ModelInfo, Reasoning, ReasoningBlock, ToolCall } from '@desk/protocol';
import { newId } from '../ids';
import type { ModelConfig } from './config';
import { apiError, classifyModelError } from './errors';
import { events, postStream } from './sse';
import type { ChatMessage, CompletionRequest, CompletionResult, CompletionUsage, ContentPart } from './types';

/**
 * The OpenAI Responses API (`<base>/responses`, which the local proxy serves for GPT models). Asked not to store
 * anything (`store: false`), it returns the model's reasoning as encrypted items a later request can carry back, so
 * the model continues from its own reasoning after a tool result instead of reasoning again from scratch.
 */

type Item = Record<string, unknown> & { type: string };

function userContent(content: string | ContentPart[]): Item[] {
  if (typeof content === 'string') return content ? [{ type: 'input_text', text: content }] : [];
  return content.flatMap((p): Item[] => (p.type === 'text' ? (p.text ? [{ type: 'input_text', text: p.text }] : []) : [{ type: 'input_image', image_url: p.image_url.url }]));
}

/**
 * Chat messages as Responses input: the system prompt as `instructions`, tool calls and results as their own items. An
 * assistant message carries its reasoning items (and its message's phase) only to the model that wrote them.
 */
export function toResponsesInput(messages: ChatMessage[], model: string): { instructions: string; input: Item[] } {
  const instructions: string[] = [];
  const input: Item[] = [];
  for (const m of messages) {
    switch (m.role) {
      case 'system':
        instructions.push(m.content);
        break;
      case 'user': {
        const content = userContent(m.content);
        if (content.length) input.push({ type: 'message', role: 'user', content });
        break;
      }
      case 'tool':
        input.push({ type: 'function_call_output', call_id: m.tool_call_id, output: m.content });
        break;
      case 'assistant': {
        const own = m.reasoning?.model === model ? m.reasoning : undefined;
        for (const b of own?.blocks ?? []) {
          if (b.type === 'encrypted_reasoning') {
            input.push({ type: 'reasoning', id: b.id, encrypted_content: b.encrypted_content, summary: b.summary.map((text) => ({ type: 'summary_text', text })) });
          }
        }
        if (m.content) {
          input.push({ type: 'message', role: 'assistant', content: [{ type: 'output_text', text: m.content }], ...(own?.phase ? { phase: own.phase } : {}) });
        }
        for (const tc of m.tool_calls ?? []) input.push({ type: 'function_call', call_id: tc.id, name: tc.function.name, arguments: tc.function.arguments || '{}' });
        break;
      }
    }
  }
  return { instructions: instructions.join('\n\n'), input };
}

/** The request body: nothing stored, reasoning returned encrypted, at the chosen effort. */
export function responsesBody(req: CompletionRequest, info: ModelInfo): Record<string, unknown> {
  const { instructions, input } = toResponsesInput(req.messages, req.model);
  const effort = req.reasoningEffort && info.reasoning_efforts.includes(req.reasoningEffort) ? req.reasoningEffort : undefined;
  return {
    model: req.model,
    ...(instructions ? { instructions } : {}),
    input,
    ...(req.tools.length ? { tools: req.tools.map((t) => ({ type: 'function', name: t.function.name, description: t.function.description, parameters: t.function.parameters, strict: false })) } : {}),
    ...(effort ? { reasoning: { effort } } : {}),
    store: false,
    include: ['reasoning.encrypted_content'],
    stream: true,
  };
}

/** Reads a streamed response: its text (streamed to `onText`), function calls, encrypted reasoning and usage. */
export async function readResponsesStream(body: ReadableStream<Uint8Array>, model: string, onText?: (delta: string) => void): Promise<Omit<CompletionResult, 'usage'> & { usage?: CompletionUsage }> {
  const items: Array<Record<string, any>> = [];
  let completed: Record<string, any> | undefined;
  for await (const ev of events(body)) {
    switch (ev.type) {
      case 'response.output_text.delta':
        if (typeof ev.delta === 'string') onText?.(ev.delta);
        break;
      case 'response.output_item.done':
        if (ev.item) items.push(ev.item);
        break;
      case 'response.completed':
      case 'response.incomplete':
        completed = ev.response;
        break;
      case 'response.failed':
        throw apiError(undefined, ev.response?.error?.message ?? 'Model response failed', ev.response?.error?.code);
      case 'error':
        throw apiError(undefined, ev.message ?? ev.error?.message ?? 'Model stream error', ev.code ?? ev.error?.type);
      default:
        break;
    }
  }
  // The finished response lists every item too: it covers a stream that sent no item events.
  const output: Array<Record<string, any>> = items.length ? items : (completed?.output ?? []);
  const messages = output.filter((o) => o.type === 'message');
  const text = messages
    .flatMap((o) => (o.content ?? []) as Array<Record<string, any>>)
    .flatMap((c) => (c.type === 'output_text' && typeof c.text === 'string' ? [c.text] : []))
    .join('');
  const toolCalls: ToolCall[] = output.flatMap((o) => (o.type === 'function_call' ? [{ id: o.call_id || `call_${newId()}`, name: o.name, arguments: o.arguments || '{}' }] : []));
  const blocks = output.flatMap((o): ReasoningBlock[] =>
    o.type === 'reasoning' && typeof o.encrypted_content === 'string'
      ? [{ type: 'encrypted_reasoning', id: o.id ?? '', encrypted_content: o.encrypted_content, summary: ((o.summary ?? []) as Array<{ text?: unknown }>).flatMap((x) => (typeof x.text === 'string' ? [x.text] : [])) }]
      : [],
  );
  const phase = messages.at(-1)?.phase;
  const reasoning: Reasoning | undefined = blocks.length ? { model, blocks, ...(typeof phase === 'string' ? { phase } : {}) } : undefined;
  const u = completed?.usage;
  const cached = u?.input_tokens_details?.cached_tokens;
  return {
    content: text.length ? text : null,
    toolCalls,
    finishReason: toolCalls.length ? 'tool_calls' : completed?.status === 'incomplete' ? 'length' : 'stop',
    ...(reasoning ? { reasoning } : {}),
    ...(u ? { usage: { prompt_tokens: u.input_tokens ?? 0, completion_tokens: u.output_tokens ?? 0, ...(cached ? { cached_tokens: cached } : {}), estimated: false } } : {}),
  };
}

/** One Responses call. Throws a ModelError. */
export async function completeWithResponses(
  config: ModelConfig,
  info: ModelInfo,
  req: CompletionRequest,
  opts: { signal?: AbortSignal; onText?: (delta: string) => void } = {},
): Promise<Omit<CompletionResult, 'usage'> & { usage?: CompletionUsage }> {
  try {
    const body = await postStream(config, '/responses', responsesBody(req, info), opts.signal);
    return await readResponsesStream(body, req.model, opts.onText);
  } catch (err) {
    // A user's stop aborts the request: report it as aborted, not as the timeout's error.
    if (opts.signal?.aborted) throw classifyModelError(Object.assign(new Error('Request aborted'), { name: 'AbortError' }));
    throw classifyModelError(err);
  }
}
