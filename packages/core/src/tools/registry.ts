import { mkdir, writeFile } from 'node:fs/promises';
import { join } from 'node:path';
import { z } from 'zod';
import type { ToolCall } from '@desk/protocol';
import type { ToolSpec } from '../model/types';
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
  const dir = join(ctx.workspace, '.desk', 'outputs');
  await mkdir(dir, { recursive: true });
  const file = join(dir, `${ctx.toolCallId.replace(/[^\w-]/g, '_')}.txt`);
  await writeFile(file, content);
  const half = MAX_TOOL_OUTPUT_CHARS / 2;
  return `${content.slice(0, half)}\n\n[... ${content.length - MAX_TOOL_OUTPUT_CHARS} characters truncated; full output saved to ${file} ...]\n\n${content.slice(-half)}`;
}

/** Validates and executes one tool call. Never throws. */
export async function executeToolCall(tools: Tool[], call: ToolCall, ctx: ToolContext): Promise<ToolResult> {
  const tool = tools.find((t) => t.name === call.name);
  if (!tool) return { status: 'error', content: `Unknown tool: ${call.name}` };

  let raw: unknown;
  try {
    raw = JSON.parse(call.arguments || '{}');
  } catch {
    return { status: 'error', content: `Invalid JSON arguments for ${call.name}` };
  }
  const parsed = tool.input.safeParse(raw);
  if (!parsed.success) {
    return { status: 'error', content: `Invalid arguments for ${call.name}:\n${z.prettifyError(parsed.error)}` };
  }

  try {
    const out = await tool.execute(parsed.data, ctx);
    const res = typeof out === 'string' ? { content: out } : out;
    const content = await truncateOutput(res.content, ctx);
    return res.yield ? { status: 'ok', content, yield: res.yield } : { status: 'ok', content };
  } catch (err) {
    if (err instanceof ToolDenied) return { status: 'denied', content: err.message };
    return { status: 'error', content: err instanceof Error ? err.message : String(err) };
  }
}
