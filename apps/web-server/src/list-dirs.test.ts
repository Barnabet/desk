import { mkdirSync, mkdtempSync, realpathSync, rmSync, symlinkSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { afterEach, beforeEach, describe, expect, it } from 'vitest';
import { listDirs, type ListDirsDeps } from './list-dirs';

let root: string;
beforeEach(() => void (root = realpathSync(mkdtempSync(join(tmpdir(), 'desk-dirs-')))));
afterEach(() => rmSync(root, { recursive: true, force: true }));

/** home (with the data dir inside, as on macOS), a source outside home, and a folder outside both. */
function layout(extra: Partial<ListDirsDeps> = {}) {
  const home = join(root, 'home');
  const dataDir = join(home, 'Library', 'Application Support', 'Desk');
  const outside = join(root, 'outside');
  const source = join(root, 'work', 'repo');
  for (const d of [join(home, 'code', 'app'), join(home, 'Documents'), join(home, '.claude', 'skills', 'pdf'), join(dataDir, 'projects'), join(outside, 'secret'), join(source, 'src')]) {
    mkdirSync(d, { recursive: true });
  }
  writeFileSync(join(home, 'notes.txt'), 'x');
  symlinkSync(join(home, 'code'), join(home, 'code-link'), 'junction');
  symlinkSync(outside, join(home, 'escape'), 'junction');
  symlinkSync(dataDir, join(home, 'desk-data'), 'junction');
  const deps: ListDirsDeps = { home, dataDir, sources: async () => [source], ...extra };
  return { home, dataDir, outside, source, deps };
}

const names = (l: { dirs: Array<{ name: string }> }) => l.dirs.map((d) => d.name);

describe('fs.listDirs', () => {
  it("lists home's folders, sorted, without files or hidden ones, and resolves symlinks inside the allowed folders", async () => {
    const { home, deps } = layout();
    const l = await listDirs({}, deps);
    expect(l.path).toBe(home);
    expect(l.parent).toBeNull();
    expect(names(l)).toEqual(['code', 'code-link', 'Documents', 'Library']);
    expect(l.dirs.find((d) => d.name === 'code-link')?.path).toBe(join(home, 'code'));
    expect((await listDirs({ path: '~' }, deps)).path).toBe(home);
    expect(names(await listDirs({ path: '~/code' }, deps))).toEqual(['app']);
    expect((await listDirs({ path: '~/code' }, deps)).parent).toBe(home);
  });

  it('shows hidden folders only when asked, and lists a hidden folder typed by path', async () => {
    const { deps } = layout();
    expect(names(await listDirs({ hidden: true }, deps))).toEqual(['.claude', 'code', 'code-link', 'Documents', 'Library']);
    expect(names(await listDirs({ path: '~/.claude/skills' }, deps))).toEqual(['pdf']);
  });

  it('never lists the data dir or anything in it, and hides it from its parent', async () => {
    const { home, dataDir, deps } = layout();
    for (const path of [dataDir, join(dataDir, 'projects'), join(home, 'desk-data')]) {
      await expect(listDirs({ path }, deps), path).rejects.toMatchObject({ code: 'not_allowed' });
    }
    expect(names(await listDirs({ path: join(home, 'Library', 'Application Support') }, deps))).toEqual([]);
  });

  it('refuses folders outside home and the sources, symlinks included, and says why', async () => {
    const { home, outside, deps } = layout();
    await expect(listDirs({ path: outside }, deps)).rejects.toMatchObject({ code: 'not_allowed' });
    await expect(listDirs({ path: join(home, 'escape') }, deps)).rejects.toMatchObject({ code: 'not_allowed' });
    await expect(listDirs({ path: root }, deps)).rejects.toMatchObject({ code: 'not_allowed' });
    await expect(listDirs({ path: 'code' }, deps)).rejects.toMatchObject({ code: 'invalid_path' });
    await expect(listDirs({ path: join(home, 'missing') }, deps)).rejects.toMatchObject({ code: 'not_found' });
    await expect(listDirs({ path: join(home, 'notes.txt') }, deps)).rejects.toMatchObject({ code: 'not_a_folder' });
  });

  it('lists a source outside home, with a parent only while it stays inside the source', async () => {
    const { source, deps } = layout();
    const top = await listDirs({ path: source }, deps);
    expect(names(top)).toEqual(['src']);
    expect(top.parent).toBeNull();
    expect((await listDirs({ path: join(source, 'src') }, deps)).parent).toBe(source);
  });

  it('still lists home when the sources cannot be read (deskd is down)', async () => {
    const { source, deps } = layout({ sources: async () => Promise.reject(new Error('Desk is not running.')) });
    expect(names(await listDirs({}, deps))).toContain('code');
    await expect(listDirs({ path: source }, deps)).rejects.toMatchObject({ code: 'not_allowed' });
  });

  it.runIf(process.platform === 'darwin')('refuses the data dir typed in another case (macOS folders ignore case)', async () => {
    const { home, deps } = layout();
    await expect(listDirs({ path: join(home, 'library', 'application support', 'desk') }, deps)).rejects.toMatchObject({ code: 'not_allowed' });
  });
});
