import { execFileSync } from 'node:child_process';
import { linkSync, lstatSync, mkdirSync, readFileSync, renameSync, rmSync, symlinkSync, unlinkSync, writeFileSync } from 'node:fs';
import { mkdtemp, realpath, rm } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { afterEach, beforeEach, describe, expect, it } from 'vitest';
import { testToolContext } from '../testing/context';
import { readAgentFile, readAgentFileSync, writeAgentFile } from './agent-files';
import { readFileTool, writeFileTool } from './fs';
import { NO_SANDBOX, sandboxGuard, type SandboxGuard } from './sandbox';
import { ToolDenied } from './types';

let root: string;
let data: string;
let work: string;
let secret: string;
let guard: SandboxGuard;
beforeEach(async () => {
  root = await realpath(await mkdtemp(join(tmpdir(), 'desk-agent-files-')));
  data = join(root, 'data');
  work = join(data, 'workspaces', 't1');
  secret = join(data, 'daemon.json');
  mkdirSync(work, { recursive: true });
  writeFileSync(secret, '{"token":"tok-123"}');
  guard = sandboxGuard({ dataDir: data, secrets: [secret] });
});
afterEach(async () => rm(root, { recursive: true, force: true }));

describe('reading and writing files agents control', () => {
  it('refuses a symlink at the end of the path, and a path whose folder became a symlink after the check', async () => {
    symlinkSync(secret, join(work, 'bait'));
    await expect(readAgentFile(join(work, 'bait'), guard)).rejects.toThrow(ToolDenied);
    symlinkSync(data, join(work, 'dir'));
    await expect(readAgentFile(join(work, 'dir', 'daemon.json'), guard)).rejects.toThrow(/changed while it was being opened/);
    await expect(writeAgentFile(join(work, 'dir', 'config.json'), '{}', guard)).rejects.toThrow(ToolDenied);
    await expect(writeAgentFile(join(work, 'bait'), 'x', guard)).rejects.toThrow(ToolDenied);
    expect(readFileSync(secret, 'utf8')).toBe('{"token":"tok-123"}');
  });

  it('refuses a secret by identity, even through a hard link', async () => {
    linkSync(secret, join(work, 'hard'));
    await expect(readAgentFile(join(work, 'hard'), guard)).rejects.toThrow(/holds Desk credentials/);
    await expect(writeAgentFile(join(work, 'hard'), 'x', guard)).rejects.toThrow(/holds Desk credentials/);
    expect(() => readAgentFileSync(join(work, 'hard'), lstatSync(join(work, 'hard')), guard)).toThrow(/holds Desk credentials/);
    expect(readFileSync(secret, 'utf8')).toBe('{"token":"tok-123"}');
  });

  it('never changes the file a swapped path led to', async () => {
    const outside = join(root, 'outside');
    mkdirSync(outside);
    writeFileSync(join(outside, 'notes.txt'), 'keep me');
    // The folder is swapped for a symlink after the caller checked the path.
    symlinkSync(outside, join(work, 'swapped'));
    await expect(writeAgentFile(join(work, 'swapped', 'notes.txt'), 'overwritten', guard)).rejects.toThrow(ToolDenied);
    expect(readFileSync(join(outside, 'notes.txt'), 'utf8')).toBe('keep me');
  });

  it.skipIf(process.platform === 'win32')('refuses FIFOs and other non-files at once instead of waiting on them', async () => {
    const fifo = join(work, 'notes.txt');
    execFileSync('mkfifo', [fifo]);
    await expect(readAgentFile(fifo, guard)).rejects.toThrow(/Not a regular file/);
    await expect(writeAgentFile(fifo, 'x', guard)).rejects.toThrow();
    expect(() => readAgentFileSync(fifo, lstatSync(fifo), guard)).toThrow(/Not a regular file/);
    await expect(readAgentFile(work, guard)).rejects.toThrow(/Not a regular file/);
  });

  it('reads and writes ordinary files', async () => {
    await writeAgentFile(join(work, 'a.txt'), 'hello', guard);
    await writeAgentFile(join(work, 'a.txt'), 'hi', guard);
    expect((await readAgentFile(join(work, 'a.txt'), guard)).toString()).toBe('hi');
    expect(readAgentFileSync(join(work, 'a.txt'), lstatSync(join(work, 'a.txt')), guard).toString()).toBe('hi');
  });

  it('holds while an agent swaps a file for a symlink to the secret in a loop', async () => {
    const ctx = testToolContext(work, { readRoots: [work], writeRoots: [work], sandbox: { ...NO_SANDBOX, guard } });
    const bait = join(work, 'bait.txt');
    const decoy = join(work, 'decoy.txt');
    writeFileSync(bait, 'harmless');
    let flip = false;
    const swapper = setInterval(() => {
      // The same race an agent's background job can run: a regular file one moment, a symlink the next.
      try {
        if (flip) {
          unlinkSync(bait);
          writeFileSync(decoy, 'harmless');
          renameSync(decoy, bait);
        } else {
          unlinkSync(bait);
          symlinkSync(secret, bait);
        }
      } catch {
        // a read or write in flight may hold the name; try again next tick
      }
      flip = !flip;
    }, 0);
    try {
      for (let i = 0; i < 400; i++) {
        const out = await readFileTool.execute({ path: 'bait.txt' }, ctx).catch((err: unknown) => String(err));
        expect(String(out)).not.toContain('tok-123');
        await writeFileTool.execute({ path: 'bait.txt', content: 'PWNED' }, ctx).catch(() => undefined);
        await new Promise((r) => setImmediate(r));
      }
    } finally {
      clearInterval(swapper);
    }
    expect(readFileSync(secret, 'utf8')).toBe('{"token":"tok-123"}');
    rmSync(bait, { force: true });
  });
});
