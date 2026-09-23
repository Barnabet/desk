import { realpath } from 'node:fs/promises';
import { basename, dirname, isAbsolute, join, resolve, sep } from 'node:path';
import { ToolDenied } from './types';

async function realpathLenient(abs: string): Promise<string> {
  let current = abs;
  const missing: string[] = [];
  for (;;) {
    try {
      const real = await realpath(current);
      return missing.length ? join(real, ...missing.reverse()) : real;
    } catch (err) {
      if ((err as NodeJS.ErrnoException).code !== 'ENOENT') throw err;
      const parent = dirname(current);
      if (parent === current) throw err;
      missing.push(basename(current));
      current = parent;
    }
  }
}

/** Resolves `p` (following symlinks) and ensures it lies within one of `roots`. */
export async function resolveInside(p: string, roots: string[], cwd: string): Promise<string> {
  const abs = isAbsolute(p) ? resolve(p) : resolve(cwd, p);
  const real = await realpathLenient(abs);
  // Roots that no longer exist (e.g. a deleted source folder) are ignored rather than failing every call.
  const realRoots = (await Promise.allSettled(roots.map((r) => realpath(r)))).flatMap((r) => (r.status === 'fulfilled' ? [r.value] : []));
  if (!realRoots.some((root) => real === root || real.startsWith(root + sep))) {
    throw new ToolDenied(`Path is outside the allowed directories: ${p}`);
  }
  return real;
}
