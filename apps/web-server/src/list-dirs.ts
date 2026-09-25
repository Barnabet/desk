import { readdir, realpath, stat } from 'node:fs/promises';
import { dirname, isAbsolute, join, resolve, sep } from 'node:path';
import { UserFacingError } from '@desk/bff/server';
import type { DirListing } from './web-channels';

export type ListDirsDeps = {
  home: string;
  dataDir: string;
  /** Every project's source paths; when this fails (deskd is down) only home can be browsed. */
  sources(): Promise<string[]>;
  platform?: NodeJS.Platform;
};

async function real(path: string): Promise<string | null> {
  try {
    return await realpath(path);
  } catch {
    return null;
  }
}

/**
 * The folders in `path` (home by default), for the folder browser. Only under home or a registered source, never the
 * Desk data dir, symlinks resolved. deskd still checks any path it is given (addSource refuses the data dir, home and root).
 */
export async function listDirs(input: { path?: string | undefined; hidden?: boolean | undefined }, d: ListDirsDeps): Promise<DirListing> {
  const platform = d.platform ?? process.platform;
  const fold = platform === 'darwin' || platform === 'win32' ? (p: string) => p.toLowerCase() : (p: string) => p;
  const within = (path: string, root: string) => {
    const p = fold(path);
    const r = fold(root);
    return p === r || p.startsWith(r.endsWith(sep) ? r : r + sep);
  };

  const home = (await real(d.home)) ?? resolve(d.home);
  const dataDir = (await real(d.dataDir)) ?? resolve(d.dataDir);
  const sources = await d.sources().catch((): string[] => []);
  const roots = [home, ...(await Promise.all(sources.map(real))).filter((p): p is string => p !== null)];
  const allowed = (p: string) => !within(p, dataDir) && roots.some((r) => within(p, r));

  const typed = input.path ?? home;
  const expanded = typed === '~' ? home : typed.startsWith('~/') || typed.startsWith('~\\') ? join(home, typed.slice(2)) : typed;
  if (!isAbsolute(expanded)) throw new UserFacingError('invalid_path', 'Type a full path, such as ~/code.');
  const target = await real(expanded);
  if (!target) throw new UserFacingError('not_found', 'That folder does not exist.');
  if (within(target, dataDir)) throw new UserFacingError('not_allowed', 'Desk cannot browse its own data folder.');
  if (!roots.some((r) => within(target, r))) throw new UserFacingError('not_allowed', "Desk can browse your home folder and your projects' sources only.");
  if (!(await stat(target)).isDirectory()) throw new UserFacingError('not_a_folder', 'That is a file, not a folder.');

  const entries = await readdir(target, { withFileTypes: true }).catch(() => {
    throw new UserFacingError('unreadable', 'Desk cannot read that folder.');
  });
  const dirs: DirListing['dirs'] = [];
  for (const e of entries) {
    if (!input.hidden && e.name.startsWith('.')) continue;
    if (!e.isDirectory() && !e.isSymbolicLink()) continue;
    const path = await real(join(target, e.name));
    if (!path || !allowed(path)) continue;
    if (e.isSymbolicLink() && !(await stat(path).catch(() => null))?.isDirectory()) continue;
    dirs.push({ name: e.name, path });
  }
  dirs.sort((a, b) => a.name.localeCompare(b.name, 'en', { sensitivity: 'base' }));
  const up = dirname(target);
  return { path: target, parent: up !== target && allowed(up) ? up : null, dirs };
}
