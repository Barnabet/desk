import { appendFileSync, mkdirSync } from 'node:fs';
import { dirname } from 'node:path';

/** Appends timestamped lines to the app log. Never throws. */
export function createLog(file: string): (message: string, err?: unknown) => void {
  try {
    mkdirSync(dirname(file), { recursive: true });
  } catch {
    // Logging must not break the app.
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
