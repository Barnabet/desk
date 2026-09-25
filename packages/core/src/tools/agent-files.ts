import { closeSync, constants, fstatSync, openSync, readFileSync, realpathSync, statSync, type Stats } from 'node:fs';
import { open, realpath, stat, type FileHandle } from 'node:fs/promises';
import type { SandboxGuard } from './sandbox';
import { ToolDenied } from './types';

// deskd reads and writes files that agents control, outside the sandbox. A path checked first and opened later can be
// swapped for a symlink in between (an agent's background job can loop on it), so these open without following a
// symlink at the end, check the file they actually opened, and do the I/O through that handle. Windows has no
// O_NOFOLLOW; the identity check below still catches a swap there.
const NOFOLLOW = constants.O_NOFOLLOW ?? 0;
// A FIFO an agent made would block open() forever (and the sync copy would freeze deskd): never wait, then refuse
// anything that is not a regular file.
const NONBLOCK = constants.O_NONBLOCK ?? 0;

/** True when `st` is one of the guard's secret files. Compared by identity, so no path, link or swap gets around it. */
function isSecret(st: Stats, guard: SandboxGuard | undefined): boolean {
  return !!guard?.secrets.some((s) => {
    try {
      const x = statSync(s);
      return x.dev === st.dev && x.ino === st.ino;
    } catch {
      return false;
    }
  });
}

const secretDenied = (path: string) => new ToolDenied(`Path is off limits because it holds Desk credentials: ${path}`);
const swapDenied = (path: string) => new ToolDenied(`The path changed while it was being opened (a symlink was swapped in): ${path}`);
const notAFile = (path: string) => new Error(`Not a regular file: ${path}`);

function openFailed(path: string) {
  return (err: NodeJS.ErrnoException): never => {
    if (err.code === 'ELOOP') throw swapDenied(path);
    throw err;
  };
}

/** Throws unless `real` still resolves to itself and names the file `st` describes. */
async function assertSameFile(real: string, st: Stats): Promise<void> {
  const again = await realpath(real).catch(() => null);
  const now = again === real ? await stat(real).catch(() => null) : null;
  if (!now || now.dev !== st.dev || now.ino !== st.ino) throw swapDenied(real);
}

/**
 * Opens `real` (a real path, already checked against the agent's roots) for reading. The caller reads through the
 * handle and closes it.
 */
export async function openAgentFile(real: string, guard: SandboxGuard | undefined): Promise<{ fh: FileHandle; st: Stats }> {
  const fh = await open(real, constants.O_RDONLY | NOFOLLOW | NONBLOCK).catch(openFailed(real));
  try {
    const st = await fh.stat();
    if (!st.isFile()) throw notAFile(real);
    await assertSameFile(real, st);
    if (isSecret(st, guard)) throw secretDenied(real);
    return { fh, st };
  } catch (err) {
    await fh.close();
    throw err;
  }
}

/** Reads a file an agent controls (see `openAgentFile`). */
export async function readAgentFile(real: string, guard: SandboxGuard | undefined): Promise<Buffer> {
  const { fh } = await openAgentFile(real, guard);
  try {
    return await fh.readFile();
  } finally {
    await fh.close();
  }
}

/**
 * Writes a file an agent controls, creating it if needed. It truncates only after the checks, so a swapped path never
 * changes the file it led to; at worst a swap leaves an empty new file there.
 */
export async function writeAgentFile(real: string, data: string | Uint8Array, guard: SandboxGuard | undefined): Promise<void> {
  const fh = await open(real, constants.O_WRONLY | constants.O_CREAT | NOFOLLOW | NONBLOCK, 0o666).catch(openFailed(real));
  try {
    const st = await fh.stat();
    if (!st.isFile()) throw notAFile(real);
    await assertSameFile(real, st);
    if (isSecret(st, guard)) throw secretDenied(real);
    await fh.truncate(0);
    await fh.writeFile(data);
  } finally {
    await fh.close();
  }
}

/** `readAgentFile` for synchronous callers (the skill store copying a draft). `expected` is the entry's lstat. */
export function readAgentFileSync(real: string, expected: Stats, guard: SandboxGuard | undefined): Buffer {
  let fd: number;
  try {
    fd = openSync(real, constants.O_RDONLY | NOFOLLOW | NONBLOCK);
  } catch (err) {
    return openFailed(real)(err as NodeJS.ErrnoException);
  }
  try {
    const st = fstatSync(fd);
    if (!st.isFile()) throw notAFile(real);
    let again: string | null = null;
    try {
      again = realpathSync(real);
    } catch {
      // handled below
    }
    if (again !== real || st.dev !== expected.dev || st.ino !== expected.ino) throw swapDenied(real);
    if (isSecret(st, guard)) throw secretDenied(real);
    return readFileSync(fd);
  } finally {
    closeSync(fd);
  }
}
