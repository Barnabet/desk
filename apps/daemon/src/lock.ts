import { readFileSync, rmSync, writeFileSync } from 'node:fs';
import { ConflictError } from '@desk/core';

function isAlive(pid: number): boolean {
  try {
    process.kill(pid, 0);
    return true;
  } catch (err) {
    return (err as NodeJS.ErrnoException).code === 'EPERM';
  }
}

/** Single-instance lock holding our pid. A lock left by a dead process is taken over. Returns a release function. */
export function acquireLock(path: string): () => void {
  try {
    writeFileSync(path, String(process.pid), { flag: 'wx' });
  } catch (err) {
    if ((err as NodeJS.ErrnoException).code !== 'EEXIST') throw err;
    const pid = Number(readFileSync(path, 'utf8').trim());
    if (Number.isInteger(pid) && pid > 0 && isAlive(pid)) throw new ConflictError(`deskd is already running (pid ${pid})`);
    writeFileSync(path, String(process.pid));
  }
  return () => {
    try {
      if (readFileSync(path, 'utf8').trim() === String(process.pid)) rmSync(path);
    } catch {}
  };
}
