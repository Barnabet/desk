export type DaemonMode = 'dev' | 'packaged';
export type DaemonStatus = {
  running: boolean;
  version: string | null;
  pid: number | null;
  uptime_s: number | null;
  proxy: 'up' | 'down' | 'unknown' | null;
  mode: DaemonMode;
  bundledVersion: string;
  agent: 'installed' | 'missing' | 'unsupported';
};
