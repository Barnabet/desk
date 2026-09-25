import { randomBytes } from 'node:crypto';
import { chmodSync, mkdirSync, readFileSync, rmSync, writeFileSync } from 'node:fs';
import { join } from 'node:path';
import { z } from 'zod';
import { loginRedirectHtml } from './login';

const WebInfo = z.object({ pid: z.number().int().positive(), port: z.number().int().min(1).max(65535) });
/** `<data>/web.json`: the running desk web (deskd's sandbox guard reads its port). No secrets. */
export type WebInfo = z.infer<typeof WebInfo>;

export const WebSettings = z.object({ port: z.number().int().min(1).max(65535).optional(), notifications: z.boolean() });
/** `<data>/web-settings.json`: the chosen port (the origin, and the browser state tied to it, depends on it) and browser notifications. */
export type WebSettings = z.infer<typeof WebSettings>;
export type WebSettingsPatch = { port?: number | undefined; notifications?: boolean | undefined };

const DEFAULTS: WebSettings = { notifications: true };

export const webPaths = (dataDir: string) => ({ info: join(dataDir, 'web.json'), settings: join(dataDir, 'web-settings.json') });

/** Writes a file only its owner can read or write (0600). */
function writePrivate(file: string, text: string): void {
  writeFileSync(file, text, { mode: 0o600 });
  chmodSync(file, 0o600);
}

export function readWebInfo(dataDir: string): WebInfo | null {
  try {
    return WebInfo.parse(JSON.parse(readFileSync(webPaths(dataDir).info, 'utf8')));
  } catch {
    return null;
  }
}

export function writeWebInfo(dataDir: string, info: WebInfo): void {
  writePrivate(webPaths(dataDir).info, `${JSON.stringify(info)}\n`);
}

/** Removes web.json if it still names `pid` (a newer instance may have replaced it). */
export function removeWebInfo(dataDir: string, pid: number): void {
  if (readWebInfo(dataDir)?.pid === pid) rmSync(webPaths(dataDir).info, { force: true });
}

/** Whether a process with this pid exists (EPERM: it does, under another user). */
export function processAlive(pid: number): boolean {
  try {
    process.kill(pid, 0);
    return true;
  } catch (err) {
    return (err as NodeJS.ErrnoException).code === 'EPERM';
  }
}

/** desk web's own preferences, in the Desk data dir. */
export class WebSettingsStore {
  private value: WebSettings;

  constructor(private readonly dataDir: string) {
    this.value = this.load();
  }

  private load(): WebSettings {
    try {
      return WebSettings.parse({ ...DEFAULTS, ...(JSON.parse(readFileSync(webPaths(this.dataDir).settings, 'utf8')) as object) });
    } catch {
      return { ...DEFAULTS };
    }
  }

  get(): WebSettings {
    return { ...this.value };
  }

  /** Merges onto what is on disk, not the cached value, so a change saved through another store (the CLI's port) survives. */
  update(patch: WebSettingsPatch): WebSettings {
    const defined = Object.fromEntries(Object.entries(patch).filter(([, v]) => v !== undefined));
    this.value = WebSettings.parse({ ...this.load(), ...defined });
    mkdirSync(this.dataDir, { recursive: true });
    writePrivate(webPaths(this.dataDir).settings, `${JSON.stringify(this.value, null, 2)}\n`);
    return this.get();
  }
}

/**
 * Writes `<data>/web-login-<random>.html` (0600), a page that redirects to `link`, and returns its path. Opening this
 * file instead of the link keeps the one-time code off every command line (argv is world-readable on Linux).
 */
export function writeLoginFile(dataDir: string, link: string): string {
  const file = join(dataDir, `web-login-${randomBytes(8).toString('hex')}.html`);
  writeFileSync(file, loginRedirectHtml(link), { mode: 0o600, flag: 'wx' });
  chmodSync(file, 0o600);
  return file;
}
