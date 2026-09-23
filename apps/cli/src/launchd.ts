import { execFileSync } from 'node:child_process';
import { existsSync, mkdirSync, rmSync, writeFileSync } from 'node:fs';
import { homedir } from 'node:os';
import { join } from 'node:path';

export const LAUNCHD_LABEL = 'dev.desk.deskd';

export const plistPath = () => join(homedir(), 'Library', 'LaunchAgents', `${LAUNCHD_LABEL}.plist`);

const esc = (s: string) => s.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');

export function plistFor(o: { nodePath: string; loader: string; entry: string; dataDir: string; cwd: string }): string {
  const args = [o.nodePath, '--import', o.loader, o.entry, '--data-dir', o.dataDir].map((a) => `    <string>${esc(a)}</string>`).join('\n');
  const log = esc(join(o.dataDir, 'logs', 'deskd.launchd.log'));
  return `<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key>
  <string>${LAUNCHD_LABEL}</string>
  <key>ProgramArguments</key>
  <array>
${args}
  </array>
  <key>WorkingDirectory</key>
  <string>${esc(o.cwd)}</string>
  <key>RunAtLoad</key>
  <true/>
  <key>KeepAlive</key>
  <true/>
  <key>StandardOutPath</key>
  <string>${log}</string>
  <key>StandardErrorPath</key>
  <string>${log}</string>
</dict>
</plist>
`;
}

const domain = () => `gui/${process.getuid?.() ?? 501}`;

export function isInstalled(): boolean {
  return existsSync(plistPath());
}

/** Writes the LaunchAgent and loads it: deskd then starts at login and restarts if it exits. */
export function install(plist: string, dataDir: string): void {
  mkdirSync(join(dataDir, 'logs'), { recursive: true });
  mkdirSync(join(homedir(), 'Library', 'LaunchAgents'), { recursive: true });
  if (isInstalled()) uninstall();
  writeFileSync(plistPath(), plist);
  execFileSync('launchctl', ['bootstrap', domain(), plistPath()]);
}

export function uninstall(): void {
  try {
    execFileSync('launchctl', ['bootout', `${domain()}/${LAUNCHD_LABEL}`], { stdio: 'ignore' });
  } catch {}
  rmSync(plistPath(), { force: true });
}
