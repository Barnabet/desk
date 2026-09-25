import { clip } from '@desk/protocol';
import { STRIP_CODE, type GlobalState } from '@desk/bff/contract';
import { attentionRoute } from './notify';

export type TrayItem = { label: string; route?: string; action?: 'open' | 'quit'; enabled?: boolean } | { separator: true };

const MAX_ITEMS = 5;

function daemonLine(s: GlobalState): string {
  switch (s.connection.status) {
    case 'offline':
      return 'deskd is not running';
    case 'mismatch':
      return 'deskd needs an update';
    case 'reconnecting':
      return 'Reconnecting to deskd…';
    case 'live':
      return `deskd running · proxy ${s.system.proxy}`;
    default:
      return 'Connecting to deskd…';
  }
}

/** The menu-bar item: a count title and a menu of what needs you and what is running. */
export function trayModel(s: GlobalState): { title: string; tooltip: string; items: TrayItem[] } {
  const count = s.attention.length;
  const running = s.overview.reduce((n, p) => n + p.threads.filter((t) => t.status === 'running').length, 0);
  const offline = s.connection.status === 'offline' || s.connection.status === 'mismatch';
  const items: TrayItem[] = [
    { label: count ? `${count} need you` : 'Nothing needs you', enabled: false },
    ...s.attention.slice(0, MAX_ITEMS).map((i) => ({ label: clip(`${STRIP_CODE[i.kind]}  ${i.project_name} · ${i.title}`, 64), route: attentionRoute(i) })),
    ...(count > MAX_ITEMS ? [{ label: `and ${count - MAX_ITEMS} more…`, route: '#/attention' }] : []),
    { separator: true },
    { label: running ? `${running} thread${running === 1 ? '' : 's'} running` : 'No threads running', enabled: false },
    { label: daemonLine(s), enabled: false },
    { separator: true },
    { label: 'Open Desk', action: 'open' },
    { label: 'Quit Desk', action: 'quit' },
  ];
  return {
    title: count ? String(count) : '',
    tooltip: offline ? `Desk: ${daemonLine(s)}` : count ? `Desk: ${count} need you` : 'Desk',
    items,
  };
}
