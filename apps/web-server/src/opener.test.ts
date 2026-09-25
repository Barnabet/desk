import { chmodSync, mkdtempSync, rmSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { afterEach, beforeEach, describe, expect, it } from 'vitest';
import { openerCommand, openPath } from './opener';

describe('openerCommand', () => {
  it("uses the platform's opener with the target as its only argument", () => {
    expect(openerCommand('/tmp/Desk logs', 'darwin')).toEqual(['open', ['/tmp/Desk logs']]);
    expect(openerCommand('C:\\Desk\\logs', 'win32')).toEqual(['explorer.exe', ['C:\\Desk\\logs']]);
    expect(openerCommand('/home/u/.local/share/desk/logs', 'linux')).toEqual(['xdg-open', ['/home/u/.local/share/desk/logs']]);
  });
});

describe('openPath', () => {
  // PATH names only this folder, so the real openers are never found and nothing is ever opened.
  let bin: string;
  let path: string | undefined;
  beforeEach(() => {
    bin = mkdtempSync(join(tmpdir(), 'desk-opener-'));
    path = process.env.PATH;
    process.env.PATH = bin;
  });
  afterEach(() => {
    process.env.PATH = path;
    rmSync(bin, { recursive: true, force: true });
  });
  const PLATFORMS = ['darwin', 'win32', 'linux'] as const;

  it('says open_failed when the opener cannot run', async () => {
    for (const platform of PLATFORMS) {
      const [file] = openerCommand('', platform);
      await expect(openPath(join(bin, 'missing.html'), platform), platform).rejects.toMatchObject({
        name: 'UserFacingError',
        code: 'open_failed',
        message: expect.stringContaining(`(${file} failed)`),
      });
    }
  });

  it.runIf(process.platform !== 'win32')("takes explorer.exe's exit code 1 as opened, and exit 1 from any other opener as a failure", async () => {
    for (const platform of PLATFORMS) {
      const file = join(bin, openerCommand('', platform)[0]);
      writeFileSync(file, '#!/bin/sh\nexit 1\n');
      chmodSync(file, 0o755);
    }
    await expect(openPath(join(bin, 'page.html'), 'win32')).resolves.toBeUndefined();
    for (const platform of ['darwin', 'linux'] as const) {
      await expect(openPath(join(bin, 'page.html'), platform), platform).rejects.toMatchObject({ code: 'open_failed' });
    }
  });
});
