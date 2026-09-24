import { join } from 'node:path';

/** Shared with `desk up --install`: one LaunchAgent runs deskd for the user. */
export const LAUNCHD_LABEL = 'dev.desk.deskd';

const esc = (s: string) => s.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;');

export const plistPath = (home: string) => join(home, 'Library', 'LaunchAgents', `${LAUNCHD_LABEL}.plist`);

export function launchdPlist(o: { programArguments: string[]; env: Record<string, string>; workingDirectory: string; logFile: string }): string {
  const args = o.programArguments.map((a) => `    <string>${esc(a)}</string>`).join('\n');
  const env = Object.entries(o.env)
    .map(([k, v]) => `    <key>${esc(k)}</key>\n    <string>${esc(v)}</string>`)
    .join('\n');
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
  <key>EnvironmentVariables</key>
  <dict>
${env}
  </dict>
  <key>WorkingDirectory</key>
  <string>${esc(o.workingDirectory)}</string>
  <key>RunAtLoad</key>
  <true/>
  <key>KeepAlive</key>
  <true/>
  <key>StandardOutPath</key>
  <string>${esc(o.logFile)}</string>
  <key>StandardErrorPath</key>
  <string>${esc(o.logFile)}</string>
</dict>
</plist>
`;
}
