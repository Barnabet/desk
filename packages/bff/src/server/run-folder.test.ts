import { mkdirSync, mkdtempSync, rmSync, symlinkSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { afterEach, beforeEach, describe, expect, it } from 'vitest';
import { existingRunFolder, runFolder } from './run-folder';

const RUN = '01HZX3K9Q4T6V8W2Y5B7C0D1EF';
let dir: string;
beforeEach(() => void (dir = mkdtempSync(join(tmpdir(), 'desk-run-folder-'))));
afterEach(() => rmSync(dir, { recursive: true, force: true }));

describe('run folders', () => {
  it('builds a run folder or a step folder from ids only', () => {
    expect(runFolder('/d', RUN, 'fetch')).toBe(`/d/automation-runs/${RUN}/steps/fetch`);
    expect(runFolder('/d', RUN)).toBe(`/d/automation-runs/${RUN}`);
    for (const [run, step] of [['../x', undefined], [RUN, '../../x'], [RUN.toLowerCase(), undefined], [RUN, 'A']] as const) {
      expect(() => runFolder('/d', run, step)).toThrow('That is not a run folder.');
    }
  });

  it('refuses a folder that is gone, or is not a real directory', async () => {
    await expect(existingRunFolder(dir, RUN)).rejects.toMatchObject({ code: 'folder_missing' });
    mkdirSync(join(dir, 'automation-runs', RUN, 'steps', 'fetch'), { recursive: true });
    expect(await existingRunFolder(dir, RUN, 'fetch')).toBe(join(dir, 'automation-runs', RUN, 'steps', 'fetch'));
    writeFileSync(join(dir, 'automation-runs', RUN, 'steps', 'file'), 'x');
    await expect(existingRunFolder(dir, RUN, 'file')).rejects.toMatchObject({ code: 'folder_missing' });
    symlinkSync(tmpdir(), join(dir, 'automation-runs', RUN, 'steps', 'link'));
    await expect(existingRunFolder(dir, RUN, 'link')).rejects.toMatchObject({ code: 'folder_missing' });
  });
});
