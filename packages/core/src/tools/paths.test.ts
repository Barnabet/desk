import { mkdir, mkdtemp, rm, symlink, writeFile } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { afterEach, beforeEach, describe, expect, it } from 'vitest';
import { resolveInside } from './paths';
import { ToolDenied } from './types';

let base: string;
let ws: string;
let outside: string;
beforeEach(async () => {
  base = await mkdtemp(join(tmpdir(), 'desk-paths-'));
  ws = join(base, 'ws');
  outside = join(base, 'outside');
  await mkdir(ws);
  await mkdir(outside);
  await writeFile(join(ws, 'a.txt'), 'a');
  await writeFile(join(outside, 'secret.txt'), 's');
});
afterEach(async () => rm(base, { recursive: true, force: true }));

describe('resolveInside', () => {
  it('resolves relative paths against cwd', async () => {
    expect(await resolveInside('a.txt', [ws], ws)).toMatch(/ws\/a\.txt$/);
  });

  it('allows not-yet-existing paths inside a root', async () => {
    expect(await resolveInside('new/dir/file.txt', [ws], ws)).toMatch(/ws\/new\/dir\/file\.txt$/);
  });

  it('denies absolute paths outside roots', async () => {
    await expect(resolveInside(join(outside, 'secret.txt'), [ws], ws)).rejects.toBeInstanceOf(ToolDenied);
  });

  it('denies ../ escapes', async () => {
    await expect(resolveInside('../outside/secret.txt', [ws], ws)).rejects.toBeInstanceOf(ToolDenied);
  });

  it('denies symlinks that point outside', async () => {
    await symlink(outside, join(ws, 'link'));
    await expect(resolveInside('link/secret.txt', [ws], ws)).rejects.toBeInstanceOf(ToolDenied);
  });

  it('denies sibling directories sharing a prefix', async () => {
    await mkdir(join(base, 'ws2'));
    await expect(resolveInside(join(base, 'ws2', 'x'), [ws], ws)).rejects.toBeInstanceOf(ToolDenied);
  });
});
