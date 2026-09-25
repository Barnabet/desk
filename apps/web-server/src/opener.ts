import { execFile } from 'node:child_process';
import { UserFacingError } from '@desk/bff/server';

/** The platform's "open with the default app" command. No shell, so nothing in the path is interpreted. */
export function openerCommand(target: string, platform: NodeJS.Platform = process.platform): [string, string[]] {
  if (platform === 'darwin') return ['open', [target]];
  if (platform === 'win32') return ['explorer.exe', [target]];
  return ['xdg-open', [target]];
}

/** Opens a file or folder on this computer: the login redirect file, the logs folder. */
export function openPath(target: string, platform: NodeJS.Platform = process.platform): Promise<void> {
  const [file, args] = openerCommand(target, platform);
  return new Promise((resolve, reject) => {
    execFile(file, args, (err) => {
      // explorer.exe exits with 1 even when it opened the target.
      if (err && !(platform === 'win32' && err.code === 1)) reject(new UserFacingError('open_failed', `Could not open ${target} (${file} failed).`));
      else resolve();
    });
  });
}
