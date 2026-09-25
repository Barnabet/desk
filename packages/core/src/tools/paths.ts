import { realpath } from 'node:fs/promises';
import { basename, dirname, isAbsolute, join, resolve, sep } from 'node:path';
import { isWithin, realOrSelf, type SandboxGuard } from './sandbox';
import { ToolDenied, type ToolContext } from './types';

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

/**
 * Why a file tool may not use `real` (a real path) even inside its roots, or null (see `SandboxGuard`): secrets are never
 * read, searched or replaced, and Desk's data dir is written only inside `own`, the agent's writable roots within it.
 */
export function guardRefusal(real: string, access: 'read' | 'search' | 'write', guard: SandboxGuard | undefined, own: string[] = []): string | null {
  if (!guard) return null;
  const secret = access === 'search' ? guard.secrets.some((s) => isWithin(s, real)) : guard.secrets.includes(real);
  if (secret) return access === 'search' ? 'it contains Desk credentials' : 'it holds Desk credentials';
  const inOwn = own.some((r) => r !== guard.dataDir && isWithin(r, guard.dataDir) && isWithin(real, r));
  if (access === 'write' && isWithin(real, guard.dataDir) && !inOwn) return "it is inside Desk's data folder";
  if (access === 'write' && guard.readOnly.some((r) => isWithin(real, r))) return "it is git or ssh configuration deskd's git runs with";
  return null;
}

/** Git internals, which file tools never write: deskd runs git outside the sandbox, and git runs what its config names. */
const isGitInternal = (real: string, gitDirs: string[]) => /(^|[\\/])\.git([\\/]|$)/.test(real) || gitDirs.some((d) => isWithin(real, d));

/** `resolveInside` over the tool's read or write roots, then the guard; `search` is a read of everything under `p`. */
export async function resolveForTool(p: string, access: 'read' | 'search' | 'write', ctx: ToolContext): Promise<string> {
  const writeRoots = ctx.writeRoots ?? [ctx.workspace];
  const real = await resolveInside(p, access === 'write' ? writeRoots : ctx.readRoots, ctx.workspace);
  const why = guardRefusal(real, access, ctx.sandbox.guard, access === 'write' ? writeRoots.map(realOrSelf) : []);
  if (why) throw new ToolDenied(`Path is off limits because ${why}: ${p}`);
  if (access === 'write' && isGitInternal(real, (ctx.sandbox.gitDirs ?? []).map(realOrSelf))) throw new ToolDenied(`Path is off limits because it is inside a .git folder; use git: ${p}`);
  return real;
}
