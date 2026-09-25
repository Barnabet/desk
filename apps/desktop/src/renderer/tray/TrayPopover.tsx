import { useEffect, useMemo, useState } from 'react';
import type { ApprovalRow } from '@desk/client';
import type { AttentionItem } from '@desk/protocol';
import { href, rackOrder, STRIP_CODE, waited } from '@desk/ui-core';
import { call, DeskCallError } from '../bridge';
import { describeArgs } from '../attention/Inspector';
import { FlightStrip } from '../attention/FlightStrip';
import { Button } from '../components/Button';
import { describeError } from '../components/Toast';
import { useGlobal } from '../state/global';
import { useNow } from '../state/now';
import { miniLine } from './miniLine';
import './tray.css';

const MAX_CLEARANCE = 2;
const MAX_WAITING = 4;

const openMain = (route?: string) => void call('app.openMain', route ? { route } : {}).catch(() => {});

function useApprovalArgs(items: AttentionItem[]): Map<string, ApprovalRow> {
  const key = items.map((i) => i.id).join(',');
  const [rows, setRows] = useState<Map<string, ApprovalRow>>(new Map());
  useEffect(() => {
    let live = true;
    const projects = [...new Set(items.map((i) => i.project_id))];
    void Promise.all(projects.map((p) => call('approvals.list', { projectId: p, status: 'pending' }).catch(() => [] as ApprovalRow[]))).then((lists) => {
      if (live) setRows(new Map(lists.flat().map((a) => [a.id, a])));
    });
    return () => {
      live = false;
    };
    // Refetch only when the set of approvals changes.
  }, [key]);
  return rows;
}

function Clearance({ item, row, now }: { item: AttentionItem; row: ApprovalRow | undefined; now: number }) {
  const [busy, setBusy] = useState<string | null>(null);
  const [message, setMessage] = useState<string | null>(null);
  const cmd = row ? describeArgs(row.tool, row.arguments) : null;
  const resolve = async (decision: 'approved' | 'denied') => {
    if (!item.ref.approval_id) return;
    setBusy(decision);
    setMessage(null);
    try {
      await call('approvals.resolve', { id: item.ref.approval_id, decision });
    } catch (err) {
      setMessage(err instanceof DeskCallError && err.status === 409 ? 'Already decided.' : describeError(err).message);
      setBusy(null);
    }
  };
  return (
    <article className="tray-clearance" aria-label={`Approval: ${item.title}`}>
      <span className="tray-cap" aria-hidden="true">
        <span className="strip-code">{STRIP_CODE.approval}</span>
        <span className="strip-age">{waited(item.created_at, now)}</span>
      </span>
      <div className="tray-clearance-body">
        <span className="strip-project">
          {item.project_name} · {item.ref.thread_id ? 'thread' : 'Desk'}
        </span>
        <span className="tray-clearance-title">{item.title}</span>
        {cmd ? <code className="tray-command">{cmd.command ?? cmd.pretty.replace(/\s+/g, ' ')}</code> : null}
        <div className="tray-clearance-actions">
          <Button size="sm" variant="primary" pending={busy === 'approved'} disabled={busy !== null} onClick={() => void resolve('approved')}>
            Approve
          </Button>
          <Button size="sm" pending={busy === 'denied'} disabled={busy !== null} onClick={() => void resolve('denied')}>
            Deny
          </Button>
          <span className="grow" />
          <button type="button" className="link small" onClick={() => openMain(href({ name: 'attention', item: item.id }))}>
            Details
          </button>
        </div>
        {message ? (
          <span className="small muted" role="status">
            {message}
          </span>
        ) : null}
      </div>
    </article>
  );
}

/** The menu-bar popover: today's line, approvals you can clear in place, what else is waiting, and a way into Desk. */
export function TrayPopover() {
  const attention = useGlobal((g) => g.attention);
  const overview = useGlobal((g) => g.overview);
  const status = useGlobal((g) => g.connection.status);
  const proxy = useGlobal((g) => g.system.proxy);
  const now = useNow();
  const { flat } = useMemo(() => rackOrder(attention), [attention]);
  const clearance = flat.filter((i) => i.kind === 'approval');
  const others = flat.filter((i) => i.kind !== 'approval');
  const args = useApprovalArgs(clearance.slice(0, MAX_CLEARANCE));
  const line = useMemo(() => miniLine({ overview, attention, now }), [overview, attention, now]);
  const titles = useMemo(() => new Map(overview.flatMap((p) => p.threads.map((t) => [t.id, t.title] as const))), [overview]);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === 'o') {
        e.preventDefault();
        openMain();
      }
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, []);

  const daemon = status === 'live' ? `deskd running · proxy ${proxy}` : status === 'offline' ? 'deskd is not running' : status === 'reconnecting' ? 'Reconnecting…' : 'Connecting…';
  return (
    <div className="tray-pop">
      <header className="tray-head">
        <h1>Desk</h1>
        <span className={flat.length ? 'tray-count needs' : 'tray-count'}>{flat.length ? `${flat.length} need you` : 'All clear'}</span>
      </header>
      <div className="tray-line" role="img" aria-label={`Today: ${line.lanes.map((l) => `${l.title} ${l.status}`).join(', ') || 'no threads'}`}>
        <svg width={line.width} height={line.height} aria-hidden="true">
          <path d={`M${line.nowX} 6 V ${line.height - 20}`} stroke="#8A857B" strokeWidth="1" strokeDasharray="2 3" />
          {line.lanes.map((l) => (
            <path key={l.id} d={l.d} fill="none" stroke={l.color} strokeWidth="2" strokeDasharray={l.dashed ? '4 3' : undefined} />
          ))}
          <path d={line.trunk} stroke="#1C1B18" strokeWidth="2" />
          <circle cx={line.x0} cy={line.trunkY} r="3" fill="#1C1B18" />
          {line.lanes.map((l) =>
            l.end.kind === 'train' ? (
              <g key={`e${l.id}`}>
                <circle cx={l.end.x} cy={l.y} r="8" fill="#2F5BD3" fillOpacity="0.18" />
                <circle cx={l.end.x} cy={l.y} r="4.5" fill="#2F5BD3" />
              </g>
            ) : l.end.kind === 'signal' ? (
              <circle key={`e${l.id}`} cx={l.end.x} cy={l.y} r="5" fill="#C4441C" stroke="#F4F1EA" strokeWidth="1.5" />
            ) : l.end.kind === 'stalled' ? (
              <circle key={`e${l.id}`} cx={l.end.x} cy={l.y} r="3.5" fill="#fff" stroke="#A15C00" strokeWidth="1.8" />
            ) : l.end.kind === 'stop' ? (
              <circle key={`e${l.id}`} cx={l.end.x} cy={l.y} r="3" fill="#8A857B" />
            ) : null,
          )}
        </svg>
        <span className="tray-line-label desk" style={{ left: line.nowX + 12, top: line.trunkY - 8 }}>
          Desk
        </span>
        {line.lanes.map((l) => (
          <button key={l.id} type="button" className="tray-line-label" style={{ left: line.nowX + 12, top: l.y - 8, color: l.text }} onClick={() => openMain(href({ name: 'project', id: l.projectId, tab: 'threads', threadId: l.id }))}>
            {l.title}
          </button>
        ))}
        <span className="tray-line-now" style={{ left: line.nowX, top: line.height - 17 }}>
          now
        </span>
      </div>
      <div className="tray-body">
        {clearance.length ? (
          <>
            <span className="tray-section">CLEARANCE · {clearance.length}</span>
            {clearance.slice(0, MAX_CLEARANCE).map((i) => (
              <Clearance key={i.id} item={i} row={i.ref.approval_id ? args.get(i.ref.approval_id) : undefined} now={now} />
            ))}
          </>
        ) : null}
        {others.length || clearance.length > MAX_CLEARANCE ? (
          <>
            <span className="tray-section">ALSO WAITING · {others.length + Math.max(0, clearance.length - MAX_CLEARANCE)}</span>
            {[...clearance.slice(MAX_CLEARANCE), ...others].slice(0, MAX_WAITING).map((i) => (
              <FlightStrip key={i.id} item={i} now={now} selected={false} compact threadTitle={(id) => titles.get(id) ?? null} onSelect={() => openMain(href({ name: 'attention', item: i.id }))} />
            ))}
          </>
        ) : null}
        {!flat.length ? <p className="tray-clear">Nothing needs you. Threads keep working in the background.</p> : null}
      </div>
      <footer className="tray-foot">
        <span className={`dot ${status === 'live' ? (proxy === 'down' ? 'warn' : 'ok') : 'bad'}`} aria-hidden="true" />
        <span className="grow">{daemon}</span>
        <button type="button" className="tray-open" onClick={() => openMain()}>
          Open Desk <span className="mono muted">⌘O</span>
        </button>
      </footer>
    </div>
  );
}
