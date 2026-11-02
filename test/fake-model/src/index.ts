import { createHash } from 'node:crypto';
import { createServer, type IncomingMessage, type ServerResponse } from 'node:http';
import type { AddressInfo } from 'node:net';
import { setTimeout as sleep } from 'node:timers/promises';

export type FakeToolCall = { name: string; args: Record<string, unknown>; id?: string };
export type FakeReply =
  | {
      kind: 'reply';
      text?: string;
      /** Messages API only: thinking the reply opens with, signed (see fakeSignature). */
      thinking?: string;
      toolCalls?: FakeToolCall[];
      usage?: { prompt_tokens: number; completion_tokens: number } | 'omit';
      delayMs?: number;
    }
  | { kind: 'error'; status: number; type: string; message: string }
  | { kind: 'hang' };
export type ChatRequest = {
  model: string;
  messages: Array<Record<string, any>>;
  tools?: Array<Record<string, any>>;
  stream?: boolean;
  stream_options?: { include_usage?: boolean };
  [key: string]: unknown;
};
export type Script = (req: ChatRequest, index: number) => FakeReply;

type ReplyExtra = Omit<Extract<FakeReply, { kind: 'reply' }>, 'kind' | 'text'>;
export const text = (t: string, extra: ReplyExtra = {}): FakeReply => ({ kind: 'reply', text: t, ...extra });
export const tools = (...calls: FakeToolCall[]): FakeReply => ({ kind: 'reply', toolCalls: calls });
export const call = (name: string, args: Record<string, unknown> = {}, id?: string): FakeToolCall => ({
  name,
  args,
  ...(id ? { id } : {}),
});
export const error = (status: number, type: string, message = type): FakeReply => ({ kind: 'error', status, type, message });
export const hang = (): FakeReply => ({ kind: 'hang' });

export interface FakeModelServer {
  url: string;
  requests: ChatRequest[];
  readonly inFlight: number;
  readonly maxInFlight: number;
  setScript(script: Script | FakeReply[]): void;
  close(): Promise<void>;
}

function toScript(s: Script | FakeReply[]): Script {
  if (typeof s === 'function') return s;
  const queue = [...s];
  return () => queue.shift() ?? { kind: 'error', status: 500, type: 'script_exhausted', message: 'Fake model script exhausted' };
}

function readBody(req: IncomingMessage): Promise<string> {
  return new Promise((resolve, reject) => {
    let data = '';
    req.on('data', (c: Buffer) => (data += c.toString('utf8')));
    req.on('end', () => resolve(data));
    req.on('error', reject);
  });
}

function json(res: ServerResponse, status: number, body: unknown): void {
  res.writeHead(status, { 'content-type': 'application/json' });
  res.end(JSON.stringify(body));
}

function sse(res: ServerResponse, body: unknown): void {
  res.write(`data: ${JSON.stringify(body)}\n\n`);
}

function split(s: string, size: number): string[] {
  const out: string[] = [];
  for (let i = 0; i < s.length; i += size) out.push(s.slice(i, i + size));
  return out;
}

/** The signature the fake Messages API gives a thinking text, and checks when it comes back. */
export const fakeSignature = (thinking: string) => `sig-${createHash('sha256').update(thinking).digest('hex').slice(0, 16)}`;

/**
 * Checks a Messages request the way the Anthropic API does, as far as Desk's adapter can get it wrong: user first,
 * roles alternating, each tool result answering a tool_use of the assistant message just before, thinking signed.
 */
export function messagesProblem(messages: Array<Record<string, any>>): string | null {
  let previous: Record<string, any> | undefined;
  for (const [i, m] of messages.entries()) {
    if (i === 0 && m.role !== 'user') return 'messages: the first message must use the "user" role';
    if (previous && previous.role === m.role) return `messages.${i}: roles must alternate between "user" and "assistant"`;
    if (!Array.isArray(m.content) || !m.content.length) return `messages.${i}.content: expected a non-empty list of blocks`;
    const asked = new Set((previous?.content ?? []).filter((b: any) => b.type === 'tool_use').map((b: any) => b.id));
    for (const [k, b] of (m.content as Array<Record<string, any>>).entries()) {
      if (b.type === 'tool_result' && !asked.has(b.tool_use_id)) return `messages.${i}.content.${k}: unexpected tool_use_id ${b.tool_use_id}`;
      if (b.type === 'tool_result' && k > 0 && m.content[k - 1].type !== 'tool_result') return `messages.${i}.content.${k}: tool_result blocks must come first`;
      if (b.type === 'thinking' && (m.role !== 'assistant' || b.signature !== fakeSignature(b.thinking))) return `messages.${i}.content.${k}: Invalid \`signature\` in \`thinking\` block`;
      if (b.type === 'text' && !b.text) return `messages.${i}.content.${k}: text content blocks must be non-empty`;
    }
    previous = m;
  }
  return null;
}

/**
 * Checks message content the way Chat Completions endpoints do: a string, or parts (`text`, and `image_url` only
 * in user messages). Returns the problem, or null.
 */
export function contentProblem(messages: Array<Record<string, any>>): string | null {
  for (const [i, m] of messages.entries()) {
    if (typeof m.content === 'string' || m.content === null || m.content === undefined) continue;
    if (!Array.isArray(m.content)) return `messages[${i}].content must be a string or an array of parts`;
    for (const part of m.content as Array<Record<string, any>>) {
      if (part?.type === 'text' && typeof part.text === 'string') continue;
      if (part?.type === 'image_url' && typeof part.image_url?.url === 'string') {
        if (m.role !== 'user') return `messages[${i}]: image_url parts are only allowed in user messages`;
        continue;
      }
      return `messages[${i}]: unknown content part ${JSON.stringify(part).slice(0, 80)}`;
    }
  }
  return null;
}

export async function startFakeModel(initial: Script | FakeReply[] = [], opts: { port?: number } = {}): Promise<FakeModelServer> {
  let script = toScript(initial);
  const requests: ChatRequest[] = [];
  let inFlight = 0;
  let maxInFlight = 0;
  let callIndex = 0;
  let idCounter = 0;

  const server = createServer(async (req, res) => {
    if (req.method === 'GET' && req.url === '/v1/models') {
      return json(res, 200, { object: 'list', data: [{ id: 'fake-model', object: 'model', owned_by: 'fake' }] });
    }
    if (req.method === 'POST' && req.url === '/v1/messages') return messagesReply(req, res);
    if (req.method !== 'POST' || req.url !== '/v1/chat/completions') {
      return json(res, 404, { error: { type: 'not_found', message: 'not found' } });
    }
    let body: ChatRequest;
    try {
      body = JSON.parse(await readBody(req)) as ChatRequest;
    } catch {
      return json(res, 400, { error: { type: 'invalid_request_error', message: 'bad json' } });
    }
    requests.push(body);
    const problem = contentProblem(body.messages ?? []);
    if (problem) return json(res, 400, { error: { type: 'invalid_request_error', message: problem } });
    inFlight++;
    maxInFlight = Math.max(maxInFlight, inFlight);
    res.on('close', () => {
      inFlight--;
    });

    const reply = script(body, callIndex++);
    if (reply.kind === 'hang') return;
    if (reply.kind === 'error') return json(res, reply.status, { error: { type: reply.type, message: reply.message } });
    if (reply.delayMs) await sleep(reply.delayMs);
    if (res.destroyed) return;

    const toolCalls = (reply.toolCalls ?? []).map((c) => ({
      id: c.id ?? `call_fake_${++idCounter}`,
      name: c.name,
      arguments: JSON.stringify(c.args),
    }));
    const usage =
      reply.usage === 'omit'
        ? undefined
        : (reply.usage ?? { prompt_tokens: Math.ceil(JSON.stringify(body.messages).length / 4), completion_tokens: 10 });
    const usageBody = usage ? { ...usage, total_tokens: usage.prompt_tokens + usage.completion_tokens } : undefined;
    const finish = toolCalls.length ? 'tool_calls' : 'stop';

    if (!body.stream) {
      return json(res, 200, {
        id: 'chatcmpl-fake',
        object: 'chat.completion',
        created: 0,
        model: body.model,
        choices: [
          {
            index: 0,
            message: {
              role: 'assistant',
              content: reply.text ?? null,
              ...(toolCalls.length
                ? {
                    tool_calls: toolCalls.map((tc) => ({
                      id: tc.id,
                      type: 'function',
                      function: { name: tc.name, arguments: tc.arguments },
                    })),
                  }
                : {}),
            },
            finish_reason: finish,
          },
        ],
        ...(usageBody ? { usage: usageBody } : {}),
      });
    }

    res.writeHead(200, { 'content-type': 'text/event-stream', 'cache-control': 'no-cache', connection: 'keep-alive' });
    const chunk = (delta: object, finish_reason: string | null = null) =>
      sse(res, {
        id: 'chatcmpl-fake',
        object: 'chat.completion.chunk',
        created: 0,
        model: body.model,
        choices: [{ index: 0, delta, finish_reason }],
      });
    chunk({ role: 'assistant', content: '' });
    for (const piece of split(reply.text ?? '', 8)) chunk({ content: piece });
    toolCalls.forEach((tc, index) => {
      chunk({ tool_calls: [{ index, id: tc.id, type: 'function', function: { name: tc.name, arguments: '' } }] });
      for (const piece of split(tc.arguments, Math.max(1, Math.ceil(tc.arguments.length / 2)))) {
        chunk({ tool_calls: [{ index, function: { arguments: piece } }] });
      }
    });
    chunk({}, finish);
    if (usageBody && body.stream_options?.include_usage) {
      sse(res, { id: 'chatcmpl-fake', object: 'chat.completion.chunk', created: 0, model: body.model, choices: [], usage: usageBody });
    }
    res.write('data: [DONE]\n\n');
    res.end();
  });

  /** The Messages API: the same scripts, the request as Messages sends it (`system`, block messages), a streamed reply. */
  async function messagesReply(req: IncomingMessage, res: ServerResponse) {
    let body: ChatRequest;
    try {
      body = JSON.parse(await readBody(req)) as ChatRequest;
    } catch {
      return json(res, 400, { type: 'error', error: { type: 'invalid_request_error', message: 'bad json' } });
    }
    requests.push(body);
    const problem = messagesProblem(body.messages ?? []);
    if (problem) return json(res, 400, { type: 'error', error: { type: 'invalid_request_error', message: problem } });
    inFlight++;
    maxInFlight = Math.max(maxInFlight, inFlight);
    res.on('close', () => {
      inFlight--;
    });
    const reply = script(body, callIndex++);
    if (reply.kind === 'hang') return;
    if (reply.kind === 'error') return json(res, reply.status, { type: 'error', error: { type: reply.type, message: reply.message } });
    if (reply.delayMs) await sleep(reply.delayMs);
    if (res.destroyed) return;

    const usage = reply.usage === 'omit' ? undefined : (reply.usage ?? { prompt_tokens: Math.ceil(JSON.stringify(body.messages).length / 4), completion_tokens: 10 });
    res.writeHead(200, { 'content-type': 'text/event-stream', 'cache-control': 'no-cache', connection: 'keep-alive' });
    const send = (type: string, data: object) => res.write(`event: ${type}\ndata: ${JSON.stringify({ type, ...data })}\n\n`);
    send('message_start', {
      message: { id: 'msg_fake', type: 'message', role: 'assistant', content: [], model: body.model, ...(usage ? { usage: { input_tokens: usage.prompt_tokens, cache_read_input_tokens: 0, cache_creation_input_tokens: 0, output_tokens: 1 } } : {}) },
    });
    let index = 0;
    if (reply.thinking !== undefined) {
      send('content_block_start', { index, content_block: { type: 'thinking', thinking: '', signature: '' } });
      for (const piece of split(reply.thinking, 8)) send('content_block_delta', { index, delta: { type: 'thinking_delta', thinking: piece } });
      send('content_block_delta', { index, delta: { type: 'signature_delta', signature: fakeSignature(reply.thinking) } });
      send('content_block_stop', { index: index++ });
    }
    if (reply.text) {
      send('content_block_start', { index, content_block: { type: 'text', text: '' } });
      for (const piece of split(reply.text, 8)) send('content_block_delta', { index, delta: { type: 'text_delta', text: piece } });
      send('content_block_stop', { index: index++ });
    }
    for (const c of reply.toolCalls ?? []) {
      const args = JSON.stringify(c.args);
      send('content_block_start', { index, content_block: { type: 'tool_use', id: c.id ?? `toolu_fake_${++idCounter}`, name: c.name, input: {} } });
      for (const piece of split(args, Math.max(1, Math.ceil(args.length / 2)))) send('content_block_delta', { index, delta: { type: 'input_json_delta', partial_json: piece } });
      send('content_block_stop', { index: index++ });
    }
    send('message_delta', { delta: { stop_reason: reply.toolCalls?.length ? 'tool_use' : 'end_turn' }, ...(usage ? { usage: { output_tokens: usage.completion_tokens } } : {}) });
    send('message_stop', {});
    res.end();
  }

  await new Promise<void>((resolve) => server.listen(opts.port ?? 0, '127.0.0.1', resolve));
  const { port } = server.address() as AddressInfo;

  return {
    url: `http://127.0.0.1:${port}/v1`,
    requests,
    get inFlight() {
      return inFlight;
    },
    get maxInFlight() {
      return maxInFlight;
    },
    setScript(s) {
      script = toScript(s);
      callIndex = 0;
    },
    close: () =>
      new Promise<void>((resolve) => {
        if (!server.listening) return resolve();
        server.closeAllConnections();
        server.close(() => resolve());
      }),
  };
}
