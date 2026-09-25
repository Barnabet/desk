import { mkdir, mkdtemp, readdir, readFile, rm, symlink } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { afterEach, beforeEach, describe, expect, it } from 'vitest';
import { z } from 'zod';
import { executeToolCall, MAX_TOOL_OUTPUT_CHARS, toToolSpecs } from './registry';
import { testToolContext } from '../testing/context';
import { defineTool, ToolDenied, type ToolContext } from './types';

let ws: string;
let ctx: ToolContext;
beforeEach(async () => {
  ws = await mkdtemp(join(tmpdir(), 'desk-reg-'));
  ctx = testToolContext(ws, { toolCallId: 'toolu_1' });
});
afterEach(async () => rm(ws, { recursive: true, force: true }));

const echo = defineTool({
  name: 'echo',
  description: 'Echo text',
  input: z.object({ text: z.string(), times: z.number().int().default(1) }),
  async execute({ text, times }) {
    return text.repeat(times);
  },
});
const denied = defineTool({ name: 'denied', description: 'd', input: z.object({}), async execute() { throw new ToolDenied('not allowed'); } });
const boom = defineTool({ name: 'boom', description: 'b', input: z.object({}), async execute() { throw new Error('kaboom'); } });
const finish = defineTool({
  name: 'finish',
  description: 'f',
  input: z.object({}),
  async execute() {
    return { content: 'bye', yield: { status: 'done' as const, reason: 'finished' } };
  },
});
const all = [echo, denied, boom, finish];
const run = (name: string, args: unknown) => executeToolCall(all, { id: 'toolu_1', name, arguments: JSON.stringify(args) }, ctx);

describe('toToolSpecs', () => {
  it('produces function specs with input-side JSON schema and no $schema key', () => {
    const [spec] = toToolSpecs([echo]);
    expect(spec?.function.name).toBe('echo');
    expect(spec?.function.parameters).not.toHaveProperty('$schema');
    expect(spec?.function.parameters).toMatchObject({ type: 'object', required: ['text'] });
  });
});

describe('executeToolCall', () => {
  it('runs a tool with validated, defaulted input', async () => {
    expect(await run('echo', { text: 'ab', times: 2 })).toEqual({ status: 'ok', content: 'abab' });
    expect(await run('echo', { text: 'x' })).toEqual({ status: 'ok', content: 'x' });
  });

  it('reports unknown tools, bad JSON and invalid args as errors', async () => {
    expect((await run('nope', {})).status).toBe('error');
    const bad = await executeToolCall(all, { id: 'x', name: 'echo', arguments: '{not json' }, ctx);
    expect(bad).toMatchObject({ status: 'error', content: expect.stringContaining('Invalid JSON') });
    expect(await run('echo', { text: 1 })).toMatchObject({ status: 'error', content: expect.stringContaining('Invalid arguments') });
  });

  it('maps ToolDenied to denied and other throws to error', async () => {
    expect(await run('denied', {})).toEqual({ status: 'denied', content: 'not allowed' });
    expect(await run('boom', {})).toEqual({ status: 'error', content: 'kaboom' });
  });

  it('passes through yields', async () => {
    expect(await run('finish', {})).toEqual({ status: 'ok', content: 'bye', yield: { status: 'done', reason: 'finished' } });
  });

  it('truncates long output and saves the full text in the workspace', async () => {
    const big = 'x'.repeat(MAX_TOOL_OUTPUT_CHARS + 5000);
    const r = await run('echo', { text: big });
    expect(r.content.length).toBeLessThan(MAX_TOOL_OUTPUT_CHARS + 500);
    expect(r.content).toContain('characters truncated');
    expect(await readFile(join(ws, '.desk', 'outputs', 'toolu_1.txt'), 'utf8')).toBe(big);
  });

  it('never saves the full output through a symlinked outputs folder', async () => {
    const elsewhere = await mkdtemp(join(tmpdir(), 'desk-elsewhere-'));
    try {
      await mkdir(join(ws, '.desk'), { recursive: true });
      await symlink(elsewhere, join(ws, '.desk', 'outputs'));
      const r = await run('echo', { text: 'y'.repeat(MAX_TOOL_OUTPUT_CHARS + 10) });
      expect(r.content).toContain('the full output could not be saved');
      expect(await readdir(elsewhere)).toEqual([]);
    } finally {
      await rm(elsewhere, { recursive: true, force: true });
    }
  });
});
