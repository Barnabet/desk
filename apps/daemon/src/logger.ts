import { appendFileSync, mkdirSync } from 'node:fs';
import { dirname } from 'node:path';

export type Logger = { info(msg: string): void; error(msg: string, err?: unknown): void };

/** Timestamped lines to stderr and, when given, appended to a log file. */
export function createLogger(file?: string, echo = true): Logger {
  if (file) mkdirSync(dirname(file), { recursive: true });
  const write = (level: string, msg: string) => {
    const line = `${new Date().toISOString()} ${level} ${msg}\n`;
    if (echo) process.stderr.write(line);
    if (file) appendFileSync(file, line);
  };
  return {
    info: (msg) => write('INFO', msg),
    error: (msg, err) => write('ERROR', err ? `${msg}: ${err instanceof Error ? (err.stack ?? err.message) : String(err)}` : msg),
  };
}
