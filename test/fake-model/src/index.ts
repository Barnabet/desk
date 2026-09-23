import { createServer, type IncomingMessage, type ServerResponse } from 'node:http';
import type { AddressInfo } from 'node:net';
import { setTimeout as sleep } from 'node:timers/promises';

export type FakeToolCall = { name: string; args: Record<string, unknown>; id?: string };
export type FakeReply =
  | {
      kind: 'reply';
      text?: string;
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

export async function startFakeModel(initial: Script | FakeReply[] = []): Promise<FakeModelServer> {
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

  await new Promise<void>((resolve) => server.listen(0, '127.0.0.1', resolve));
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
        server.closeAllConnections();
        server.close(() => resolve());
      }),
  };
}
