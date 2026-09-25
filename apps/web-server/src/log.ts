import { appendFileSync, mkdirSync } from 'node:fs';
import { dirname } from 'node:path';

/** Appends timestamped lines to desk web's log (`<data>/logs/web.log`). Never throws. */
export function createLog(file: string): (message: string, err?: unknown) => void {
  try {
    mkdirSync(dirname(file), { recursive: true });
  } catch {
    // Logging must not break desk web.
  }
  return (message, err) => {
    const detail = err === undefined ? '' : ` ${err instanceof Error ? (err.stack ?? err.message) : String(err)}`;
    try {
      appendFileSync(file, `${new Date().toISOString()} ${message}${detail}\n`);
    } catch {
      // Ignore.
    }
  };
}
