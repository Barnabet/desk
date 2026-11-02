import type { ModelInfo, Reasoning, ReasoningBlock, ReasoningEffort, ToolCall } from '@desk/protocol';
import { newId } from '../ids';
import type { ModelConfig } from './config';
import { apiError, classifyModelError } from './errors';
import { events, postStream } from './sse';
import type { ChatMessage, CompletionRequest, CompletionResult, CompletionUsage, ContentPart, ToolSpec } from './types';

/**
 * The Anthropic Messages API (`<base>/messages`, which the local proxy serves next to Chat Completions). Unlike Chat
 * Completions it returns the model's thinking with a signature, so a later request can carry it back: the model then
 * continues from its own reasoning after a tool result instead of reasoning again from scratch.
 */

type Block = Record<string, unknown> & { type: string };
type Message = { role: 'user' | 'assistant'; content: Block[] };
const EPHEMERAL = { type: 'ephemeral' } as const;

/** An image part as a Messages image block: a data URL becomes base64, anything else a URL source. */
function imageBlock(url: string): Block {
  const m = /^data:([^;,]+);base64,(.*)$/s.exec(url);
  return m ? { type: 'image', source: { type: 'base64', media_type: m[1], data: m[2] } } : { type: 'image', source: { type: 'url', url } };
}

function userBlocks(content: string | ContentPart[]): Block[] {
  if (typeof content === 'string') return content ? [{ type: 'text', text: content }] : [];
  return content.flatMap((p): Block[] => (p.type === 'text' ? (p.text ? [{ type: 'text', text: p.text }] : []) : [imageBlock(p.image_url.url)]));
}

/** A tool call's arguments as the object Messages takes; arguments that are not a JSON object go as `{}`. */
function toolInput(args: string): Record<string, unknown> {
  try {
    const v: unknown = JSON.parse(args || '{}');
    return v && typeof v === 'object' && !Array.isArray(v) ? (v as Record<string, unknown>) : {};
  } catch {
    return {};
  }
}

/**
 * Chat messages as a Messages request: system text apart, tool results as user `tool_result` blocks, and consecutive
 * messages of one role merged (tool results first, as Messages wants them). An assistant message carries its thinking
 * only to the model that wrote it: a signature is the model's own.
 */
export function toMessages(messages: ChatMessage[], model: string): { system: string; messages: Message[] } {
  const system: string[] = [];
  const out: Message[] = [];
  const push = (role: Message['role'], blocks: Block[]) => {
    if (!blocks.length) return;
    const last = out.at(-1);
    if (last?.role === role) last.content.push(...blocks);
    else out.push({ role, content: blocks });
  };
  for (const m of messages) {
    switch (m.role) {
      case 'system':
        system.push(m.content);
        break;
      case 'user':
        push('user', userBlocks(m.content));
        break;
      case 'tool':
        push('user', [{ type: 'tool_result', tool_use_id: m.tool_call_id, content: m.content || '(no output)' }]);
        break;
      case 'assistant': {
        const thinking = m.reasoning?.model === model ? m.reasoning.blocks.flatMap((b): Block[] => (b.type === 'encrypted_reasoning' ? [] : [{ ...b }])) : [];
        const text: Block[] = m.content ? [{ type: 'text', text: m.content }] : [];
        const calls = (m.tool_calls ?? []).map((tc): Block => ({ type: 'tool_use', id: tc.id, name: tc.function.name, input: toolInput(tc.function.arguments) }));
        push('assistant', [...thinking, ...text, ...calls]);
        break;
      }
    }
  }
  return { system: system.join('\n\n'), messages: out };
}

/** The thinking settings for a level: none turns thinking off, a level is adaptive thinking at that effort. */
function thinkingFor(effort: ReasoningEffort | undefined): Record<string, unknown> {
  if (!effort) return {};
  if (effort === 'none') return { thinking: { type: 'disabled' } };
  return { thinking: { type: 'adaptive' }, output_config: { effort } };
}

/** The request body. Cache breakpoints after the system prompt and on the last block, so each request reuses the last. */
export function messagesBody(req: CompletionRequest, info: ModelInfo): Record<string, unknown> {
  const { system, messages } = toMessages(req.messages, req.model);
  const last = messages.at(-1)?.content.at(-1);
  if (last && last.type !== 'thinking' && last.type !== 'redacted_thinking') last.cache_control = EPHEMERAL;
  const effort = req.reasoningEffort && info.reasoning_efforts.includes(req.reasoningEffort) ? req.reasoningEffort : undefined;
  return {
    model: req.model,
    max_tokens: info.max_output_tokens,
    ...(system ? { system: [{ type: 'text', text: system, cache_control: EPHEMERAL }] } : {}),
    messages,
    ...(req.tools.length ? { tools: req.tools.map((t: ToolSpec) => ({ name: t.function.name, description: t.function.description, input_schema: t.function.parameters })) } : {}),
    ...thinkingFor(effort),
    stream: true,
  };
}

const FINISH: Record<string, string> = { end_turn: 'stop', stop_sequence: 'stop', tool_use: 'tool_calls', max_tokens: 'length', refusal: 'content_filter' };

/** Reads a streamed reply: its text (streamed to `onText`), tool calls, signed thinking and usage. */
export async function readMessagesStream(body: ReadableStream<Uint8Array>, model: string, onText?: (delta: string) => void): Promise<Omit<CompletionResult, 'usage'> & { usage?: CompletionUsage }> {
  const blocks = new Map<number, Block & { json?: string }>();
  let stop = 'end_turn';
  let input = 0;
  let cacheRead = 0;
  let cacheWrite = 0;
  let output = 0;
  let sawUsage = false;
  for await (const ev of events(body)) {
    switch (ev.type) {
      case 'message_start': {
        const u = ev.message?.usage ?? {};
        input = u.input_tokens ?? 0;
        cacheRead = u.cache_read_input_tokens ?? 0;
        cacheWrite = u.cache_creation_input_tokens ?? 0;
        output = u.output_tokens ?? 0;
        sawUsage = ev.message?.usage !== undefined;
        break;
      }
      case 'content_block_start':
        blocks.set(ev.index, { ...ev.content_block });
        break;
      case 'content_block_delta': {
        const b = blocks.get(ev.index);
        const d = ev.delta ?? {};
        if (!b) break;
        if (d.type === 'text_delta') {
          b.text = `${(b.text as string | undefined) ?? ''}${d.text}`;
          onText?.(d.text);
        } else if (d.type === 'thinking_delta') b.thinking = `${(b.thinking as string | undefined) ?? ''}${d.thinking}`;
        else if (d.type === 'signature_delta') b.signature = `${(b.signature as string | undefined) ?? ''}${d.signature}`;
        else if (d.type === 'input_json_delta') b.json = `${b.json ?? ''}${d.partial_json}`;
        break;
      }
      case 'message_delta':
        if (ev.delta?.stop_reason) stop = ev.delta.stop_reason;
        if (ev.usage?.output_tokens !== undefined) output = ev.usage.output_tokens;
        // The proxy may report input counts here instead of in message_start.
        if (ev.usage?.input_tokens !== undefined) input = ev.usage.input_tokens;
        if (ev.usage?.cache_read_input_tokens !== undefined) cacheRead = ev.usage.cache_read_input_tokens;
        if (ev.usage?.cache_creation_input_tokens !== undefined) cacheWrite = ev.usage.cache_creation_input_tokens;
        sawUsage ||= ev.usage !== undefined;
        break;
      case 'error':
        throw apiError(undefined, ev.error?.message ?? 'Model stream error', ev.error?.type);
      default:
        break;
    }
  }
  const ordered = [...blocks.entries()].sort(([a], [b]) => a - b).map(([, b]) => b);
  const text = ordered.flatMap((b) => (b.type === 'text' && b.text ? [b.text as string] : [])).join('');
  const toolCalls: ToolCall[] = ordered.flatMap((b) =>
    b.type === 'tool_use' ? [{ id: (b.id as string) || `call_${newId()}`, name: b.name as string, arguments: b.json?.trim() ? b.json : JSON.stringify(b.input ?? {}) }] : [],
  );
  const thinking = ordered.flatMap((b): ReasoningBlock[] =>
    b.type === 'thinking' && typeof b.signature === 'string' && b.signature
      ? [{ type: 'thinking', thinking: (b.thinking as string | undefined) ?? '', signature: b.signature }]
      : b.type === 'redacted_thinking' && typeof b.data === 'string'
        ? [{ type: 'redacted_thinking', data: b.data }]
        : [],
  );
  const reasoning: Reasoning | undefined = thinking.length ? { model, blocks: thinking } : undefined;
  return {
    content: text.length ? text : null,
    toolCalls,
    finishReason: FINISH[stop] ?? stop,
    ...(reasoning ? { reasoning } : {}),
    ...(sawUsage
      ? { usage: { prompt_tokens: input + cacheRead + cacheWrite, completion_tokens: output, ...(cacheRead ? { cached_tokens: cacheRead } : {}), estimated: false } }
      : {}),
  };
}

/** One Messages call: the body for `req`, the response read as it streams. Throws a ModelError. */
export async function completeWithMessages(
  config: ModelConfig,
  info: ModelInfo,
  req: CompletionRequest,
  opts: { signal?: AbortSignal; onText?: (delta: string) => void } = {},
): Promise<Omit<CompletionResult, 'usage'> & { usage?: CompletionUsage }> {
  try {
    const body = await postStream(config, '/messages', messagesBody(req, info), opts.signal, { 'x-api-key': config.apiKey, 'anthropic-version': '2023-06-01' });
    return await readMessagesStream(body, req.model, opts.onText);
  } catch (err) {
    // A user's stop aborts the request: report it as aborted, not as the timeout's error.
    if (opts.signal?.aborted) throw classifyModelError(Object.assign(new Error('Request aborted'), { name: 'AbortError' }));
    throw classifyModelError(err);
  }
}
