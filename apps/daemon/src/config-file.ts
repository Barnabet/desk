import { existsSync, readFileSync, writeFileSync } from 'node:fs';
import { z } from 'zod';

const DaemonFile = z.object({
  base_url: z.string().nullable().default(null),
  notifications: z.enum(['auto', 'off']).default('auto'),
});
export type DaemonFile = z.infer<typeof DaemonFile>;

/** `<dataDir>/config.json`: daemon settings that are not secrets (the API key lives in the Keychain). */
export function loadDaemonFile(path: string): DaemonFile {
  if (!existsSync(path)) return DaemonFile.parse({});
  try {
    return DaemonFile.parse(JSON.parse(readFileSync(path, 'utf8')));
  } catch {
    return DaemonFile.parse({});
  }
}

export function saveDaemonFile(path: string, value: DaemonFile): void {
  writeFileSync(path, JSON.stringify(value, null, 2), { mode: 0o600 });
}
