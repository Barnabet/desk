import { lstat } from 'node:fs/promises';
import { join } from 'node:path';
import { UserFacingError } from './errors';

const RUN_ID = /^[0-9A-HJKMNP-TV-Z]{26}$/;
const STEP_ID = /^[a-z][a-z0-9_-]{0,39}$/;

/** A run's folder under the data dir, or one step's folder in it. Anything but a run id and a step id is refused. */
export function runFolder(dataDir: string, runId: string, stepId?: string): string {
  if (!RUN_ID.test(runId) || (stepId !== undefined && !STEP_ID.test(stepId))) throw new UserFacingError('invalid_path', 'That is not a run folder.');
  return stepId ? join(dataDir, 'automation-runs', runId, 'steps', stepId) : join(dataDir, 'automation-runs', runId);
}

/**
 * `runFolder`, once it is a real directory (not a symlink). deskd creates run and step folders; a step's script writes
 * inside its folder, never the folder itself. Retention deletes old ones.
 */
export async function existingRunFolder(dataDir: string, runId: string, stepId?: string): Promise<string> {
  const dir = runFolder(dataDir, runId, stepId);
  const st = await lstat(dir).catch(() => null);
  if (!st?.isDirectory()) throw new UserFacingError('folder_missing', "This run's folder is gone: Desk keeps the folders of each automation's last 20 runs or 30 days.");
  return dir;
}
