import { execFileSync } from 'node:child_process';
import { createHash } from 'node:crypto';
import { mkdtemp, realpath, rm, writeFile } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { bashReadonlyTool, bashTool } from '../tools/bash';
import { bashBackgroundTool, bashKillTool } from '../tools/jobs';
import { testServices, testToolContext } from '../testing/context';
import type { ToolContext } from '../tools/types';
import { endedAs, formatDuration, gitState, type ReceiptInput } from './receipts';

const git = (cwd: string, ...args: string[]) => execFileSync('git', ['-c', 'user.name=t', '-c', 'user.email=t@t', ...args], { cwd, encoding: 'utf8' }).trim();
const sha = (s: string) => createHash('sha256').update(s).digest('hex');

let ws: string;
let got: Array<{ agentId: string; r: ReceiptInput }>;
let ctx: ToolContext;
beforeEach(async () => {
  ws = await realpath(await mkdtemp(join(tmpdir(), 'desk-receipts-')));
  got = [];
  ctx = testToolContext(ws, { agentId: 'a1', toolCallId: 'call1', services: testServices({ recordReceipt: (agentId, r) => got.push({ agentId, r }) }) });
});
afterEach(async () => rm(ws, { recursive: true, force: true }));

describe('command receipts', () => {
  it('records each bash and bash_readonly command: exit, duration and a hash of the whole output', async () => {
    await bashTool.execute({ command: 'echo oops; exit 3', timeout_s: 10 }, ctx);
    await bashReadonlyTool.execute({ command: 'echo fine', timeout_s: 10 }, ctx);
    expect(got.map((g) => g.agentId)).toEqual(['a1', 'a1']);
    expect(got[0]!.r).toMatchObject({ tool: 'bash', tool_call_id: 'call1', command: 'echo oops; exit 3', cwd: ws, head: null, dirty: null, exit_code: 3, outcome: 'exit', output_bytes: 5, output_sha256: sha('oops\n') });
    expect(got[1]!.r).toMatchObject({ tool: 'bash_readonly', exit_code: 0, outcome: 'exit', output_sha256: sha('fine\n') });
    expect(got[0]!.r.duration_ms).toBeGreaterThanOrEqual(0);
    expect(Date.parse(got[0]!.r.started_at)).not.toBeNaN();
  });

  it('records a timeout', async () => {
    await bashTool.execute({ command: 'sleep 5', timeout_s: 1 }, ctx);
    expect(got[0]!.r).toMatchObject({ outcome: 'timeout' });
    expect(endedAs({ outcome: 'timeout', exit_code: null })).toBe('timed out');
  });

  it('reads the commit and whether tracked files had changes, in a git workspace', async () => {
    git(ws, 'init', '-q');
    await writeFile(join(ws, 'a.txt'), 'one\n');
    git(ws, 'add', '-A');
    git(ws, 'commit', '-qm', 'one');
    const head = git(ws, 'rev-parse', 'HEAD');
    const g = { ...ctx, git: { branch: 'desk/x', base: head } };
    await bashTool.execute({ command: 'echo clean > untracked.txt', timeout_s: 10 }, g);
    await writeFile(join(ws, 'a.txt'), 'two\n');
    await bashTool.execute({ command: 'true', timeout_s: 10 }, g);
    expect(got.map((x) => [x.r.head, x.r.dirty])).toEqual([
      [head, false], // untracked files do not count
      [head, true],
    ]);
    expect(await gitState(join(ws, '..'))).toEqual({ head: null, dirty: null });
  });

  it('records a background job when it ends, killed or not', async () => {
    await bashBackgroundTool.execute({ command: 'printf done' }, ctx);
    await vi.waitFor(() => expect(got).toHaveLength(1));
    expect(got[0]!.r).toMatchObject({ tool: 'bash_background', command: 'printf done', exit_code: 0, outcome: 'exit', output_bytes: 4, output_sha256: sha('done') });
    const started = await bashBackgroundTool.execute({ command: 'sleep 30' }, ctx);
    const id = /job_\S+/.exec(String(started))![0].replace(/\.$/, '');
    await bashKillTool.execute({ job_id: id }, ctx);
    await vi.waitFor(() => expect(got).toHaveLength(2));
    expect(got[1]!.r).toMatchObject({ tool: 'bash_background', outcome: 'killed' });
  });

  it('formats durations', () => {
    expect([formatDuration(340), formatDuration(1200), formatDuration(42_000), formatDuration(125_000)]).toEqual(['340 ms', '1.2 s', '42 s', '2 min 5 s']);
  });
});
