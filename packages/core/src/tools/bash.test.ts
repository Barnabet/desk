import { mkdtemp, realpath, rm } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { afterEach, beforeEach, describe, expect, it } from 'vitest';
import { bashTool, scrubbedEnv } from './bash';
import type { ToolContext } from './types';

let ws: string;
let ctx: ToolContext;
beforeEach(async () => {
  ws = await realpath(await mkdtemp(join(tmpdir(), 'desk-bash-')));
  ctx = { projectId: 'p', agentId: 'a', runId: 'r', toolCallId: 't', workspace: ws, readRoots: [ws], signal: new AbortController().signal };
});
afterEach(async () => rm(ws, { recursive: true, force: true }));

describe('bash', () => {
  it('runs in the workspace and reports exit code', async () => {
    expect(await bashTool.execute({ command: 'pwd', timeout_s: 10 }, ctx)).toBe(`[exit code 0]\n${ws}\n`);
    expect(await bashTool.execute({ command: 'echo oops >&2; exit 3', timeout_s: 10 }, ctx)).toBe('[exit code 3]\noops\n');
  });

  it('kills the process group on timeout', async () => {
    const started = Date.now();
    const out = await bashTool.execute({ command: 'sleep 30 & sleep 30; echo never', timeout_s: 1 }, ctx);
    expect(out).toMatch(/^\[timed out after 1s\]/);
    expect(Date.now() - started).toBeLessThan(8000);
  });

  it('kills the process on abort', async () => {
    const controller = new AbortController();
    setTimeout(() => controller.abort(), 100);
    const out = await bashTool.execute({ command: 'sleep 30', timeout_s: 60 }, { ...ctx, signal: controller.signal });
    expect(out).toMatch(/^\[aborted\]/);
  });

  it('does not leak daemon env vars into commands', async () => {
    process.env.DESK_TEST_SECRET = 'leaky';
    try {
      const out = await bashTool.execute({ command: 'echo "[$DESK_TEST_SECRET][$DESK_WORKSPACE]"', timeout_s: 10 }, ctx);
      expect(out).toBe(`[exit code 0]\n[][${ws}]\n`);
    } finally {
      delete process.env.DESK_TEST_SECRET;
    }
  });
});

describe('scrubbedEnv', () => {
  it('keeps only safe keys plus DESK_WORKSPACE', () => {
    expect(scrubbedEnv('/w', { PATH: '/bin', HOME: '/h', OPENAI_API_KEY: 'x', CLIPROXY_API_KEY: 'y' })).toEqual({
      PATH: '/bin',
      HOME: '/h',
      DESK_WORKSPACE: '/w',
    });
  });
});
