import { mkdir } from 'node:fs/promises';
import { join } from 'node:path';
import { z } from 'zod';
import type { ToolCall } from '@desk/protocol';
import type { ToolSpec } from '../model/types';
import { writeAgentFile } from './agent-files';
import { resolveForTool } from './paths';
import { ToolDenied, type Tool, type ToolContext, type ToolResult } from './types';

export const MAX_TOOL_OUTPUT_CHARS = 20_000;

export function toToolSpecs(tools: Tool[]): ToolSpec[] {
  return tools.map((t) => {
    const { $schema: _ignored, ...parameters } = z.toJSONSchema(t.input, { io: 'input' }) as Record<string, unknown>;
    return { type: 'function', function: { name: t.name, description: t.description, parameters } };
  });
}

async function truncateOutput(content: string, ctx: ToolContext): Promise<string> {
  if (content.length <= MAX_TOOL_OUTPUT_CHARS) return content;
  const half = MAX_TOOL_OUTPUT_CHARS / 2;
  const cut = `${content.length - MAX_TOOL_OUTPUT_CHARS} characters truncated`;
  // The agent controls its workspace, `.desk` included: the file is placed like any write_file, so a symlinked
  // folder cannot send it elsewhere.
  const saved = await (async () => {
    const dir = join(ctx.workspace, '.desk', 'outputs');
    await mkdir(dir, { recursive: true });
    const file = await resolveForTool(join(dir, `${ctx.toolCallId.replace(/[^\w-]/g, '_')}.txt`), 'write', { ...ctx, writeRoots: [ctx.workspace] });
    await writeAgentFile(file, content, ctx.sandbox.guard);
    return file;
  })().catch(() => null);
  const note = saved ? `${cut}; full output saved to ${saved}` : `${cut}; the full output could not be saved`;
  return `${content.slice(0, half)}\n\n[... ${note} ...]\n\n${content.slice(-half)}`;
}

export type PreparedCall = { ok: true; tool: Tool; input: unknown } | { ok: false; result: ToolResult };

/** Resolves the tool and validates arguments without executing anything. */
export function prepareToolCall(tools: Tool[], call: ToolCall): PreparedCall {
  const tool = tools.find((t) => t.name === call.name);
  if (!tool) return { ok: false, result: { status: 'error', content: `Unknown tool: ${call.name}` } };

  let raw: unknown;
  try {
    raw = JSON.parse(call.arguments || '{}');
  } catch {
    return { ok: false, result: { status: 'error', content: `Invalid JSON arguments for ${call.name}` } };
  }
  const parsed = tool.input.safeParse(raw);
  if (!parsed.success) {
    return { ok: false, result: { status: 'error', content: `Invalid arguments for ${call.name}:\n${z.prettifyError(parsed.error)}` } };
  }
  return { ok: true, tool, input: parsed.data };
}

/** Executes an already-validated call. Never throws. */
export async function runPreparedTool(tool: Tool, input: unknown, ctx: ToolContext): Promise<ToolResult> {
  try {
    const out = await tool.execute(input, ctx);
    const res = typeof out === 'string' ? { content: out } : out;
    const content = await truncateOutput(res.content, ctx);
    return { status: 'ok', content, ...(res.yield ? { yield: res.yield } : {}), ...(res.images?.length ? { images: res.images } : {}) };
  } catch (err) {
    if (err instanceof ToolDenied) return { status: 'denied', content: err.message };
    return { status: 'error', content: err instanceof Error ? err.message : String(err) };
  }
}

/** Validates and executes one tool call (no policy check). Never throws. */
export async function executeToolCall(tools: Tool[], call: ToolCall, ctx: ToolContext): Promise<ToolResult> {
  const prepared = prepareToolCall(tools, call);
  return prepared.ok ? runPreparedTool(prepared.tool, prepared.input, ctx) : prepared.result;
}
