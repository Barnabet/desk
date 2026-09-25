import { useEffect, useMemo, useRef, useState } from 'react';
import type { ThreadView } from '@desk/client';
import { call } from '../bridge';
import { AnsweringBadge } from '../components/AnsweringBadge';
import { Button } from '../components/Button';
import { ConfirmDialog } from '../components/ConfirmDialog';
import { EmptyState } from '../components/EmptyState';
import { SkillBadge } from '../components/SkillBadge';
import { statusLabel } from '../components/StatusChip';
import { toast, toastError } from '../components/Toast';
import { clock, duration } from '../format';
import { href } from '../router';
import { useGlobal } from '../state/global';
import { useNow } from '../state/now';
import { useTranscript, type SessionState } from '../state/session';
import { answeringLabel, waitLabel } from '../waits';
import { narrate, stopsOf } from './route';
import { RouteView } from './RouteView';
import { DiffTab } from './tabs/DiffTab';
import { FilesTab } from './tabs/FilesTab';
import { ResultTab } from './tabs/ResultTab';
import { SkillDraftsTab } from './tabs/SkillDraftsTab';
import { tokens, usageByModel, UsageTab } from './tabs/UsageTab';
import { Transcript, type ComposerMode, type Depth } from './Transcript';

type Tab = 'route' | 'result' | 'diff' | 'files' | 'drafts' | 'usage';
const TABS: Array<[Tab, string]> = [
  ['route', 'Route'],
  ['result', 'Result'],
  ['diff', 'Diff'],
  ['files', 'Files'],
  ['drafts', 'Skill drafts'],
  ['usage', 'Usage'],
];

const LIVE = new Set(['running', 'waiting', 'queued']);
const FINISHED = new Set(['done', 'failed', 'cancelled']);
/** Statuses whose box asks first: the thread answers from its context without reopening (design spec §4.8, §8 item 5). */
const ASKABLE = new Set(['done', 'failed', 'idle']);

function useDepth(): [Depth, (d: Depth) => void] {
  const [d, setD] = useState<Depth>(() => {
    try {
      return localStorage.getItem('desk.transcriptDepth') === 'steps' ? 'steps' : 'narrative';
    } catch {
      return 'narrative';
    }
  });
  return [
    d,
    (v) => {
      setD(v);
      try {
        localStorage.setItem('desk.transcriptDepth', v);
      } catch {
        // A convenience only.
      }
    },
  ];
}

/** One thread: its route, tabs and transcript. `at` (an event id, from `?at=`) selects the stop that holds it. */
export function ThreadDetail({ s, thread, at }: { s: SessionState; thread: ThreadView; at?: number }) {
  const project = s.project!;
  const projectId = project.project.id;
  const now = useNow();
  const proxyDown = useGlobal((g) => g.system.proxy) === 'down';
  const attention = useGlobal((g) => g.attention);
  const transcript = useTranscript(s, projectId, thread.id);
  const rows = useMemo(() => narrate(transcript.entries), [transcript.entries]);
  const stops = useMemo(() => stopsOf(rows), [rows]);
  const [selected, setSelected] = useState<number | null>(null);
  const [tab, setTab] = useState<Tab>('route');
  const [dir, setDir] = useState('');
  const [depth, setDepth] = useDepth();
  const [confirm, setConfirm] = useState<'stop' | 'archive' | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  /** The `at` already applied: its stop is selected once, so the user's own selection is not overridden later. */
  const openedAt = useRef<number | null>(null);
  useEffect(() => {
    setSelected(null);
    setTab('route');
    setDir('');
    openedAt.current = null;
  }, [thread.id]);
  useEffect(() => {
    if (at === undefined || openedAt.current === at) return;
    const stop = stops.find((x) => x.entries.some((e) => e.id === `e:${at}`));
    if (!stop) return;
    openedAt.current = at;
    setTab('route');
    setSelected(stop.n);
  }, [at, stops]);

  const threadEvents = useMemo(() => s.events.filter((e) => e.agent_id === thread.id), [s.events, thread.id]);
  const drafts = useMemo(() => {
    for (let i = threadEvents.length - 1; i >= 0; i--) {
      const e = threadEvents[i]!;
      if (e.type === 'agent.result') return e.payload.skill_drafts ?? [];
    }
    return [];
  }, [threadEvents]);
  const usage = useMemo(() => usageByModel(threadEvents, thread.id), [threadEvents, thread.id]);
  const version = String(threadEvents.at(-1)?.id ?? 0);

  const rounds = project.project.settings.review_rounds;
  const label = statusLabel(thread.status, thread.reason, proxyDown);
  const current = selected ?? stops.at(-1)?.n ?? null;
  const archived = !!thread.archived_at;
  // A cancelled thread keeps Steer: an Ask to it would only be an ordinary message (design spec §3.2, rule 4.1).
  const composer: ComposerMode = archived
    ? { kind: 'off', hint: 'This thread is archived.' }
    : ASKABLE.has(thread.status)
      ? { kind: 'ask', reopen: thread.status === 'idle' ? 'Resume' : 'Reopen' }
      : { kind: 'steer', hint: thread.status === 'cancelled' ? 'The thread wakes up to read this. For new work, message Desk.' : 'For new work, message Desk.' };
  // What it waits on, and whether it is answering, come from the message fold, never the status reason (design spec §8).
  const wait = thread.status === 'waiting' ? waitLabel(s.messages, thread.id, attention, now) : null;
  const answering = answeringLabel(s.messages, thread.id);

  const act = async (what: 'stop' | 'archive' | 'skill') => {
    setConfirm(null);
    setBusy(what);
    try {
      if (what === 'stop') await call('threads.stop', { id: thread.id });
      else if (what === 'archive') await call('threads.archive', { id: thread.id });
      else {
        await call('projects.send', { id: projectId, text: `Turn what the thread "${thread.title ?? thread.id}" did into a reusable skill.` });
        toast({ tone: 'info', message: 'Asked Desk to turn this thread into a skill.' });
      }
    } catch (err) {
      toastError(err);
    } finally {
      setBusy(null);
    }
  };

  const actions = (
    <>
      {LIVE.has(thread.status) ? (
        <Button size="sm" pending={busy === 'stop'} onClick={() => setConfirm('stop')}>
          Stop
        </Button>
      ) : null}
      {FINISHED.has(thread.status) && !archived ? (
        <Button size="sm" pending={busy === 'archive'} onClick={() => setConfirm('archive')}>
          Archive
        </Button>
      ) : null}
    </>
  );

  return (
    <div className="thread-detail">
      <div className="thread-main">
        <div className="thread-head">
          <a className="small" href={href({ name: 'project', id: projectId, tab: 'threads' })}>
            ← All threads
          </a>
          <h1>{thread.title ?? 'Untitled thread'}</h1>
          <p className="thread-status-line">
            <span className={`tone-${label.tone}`}>{wait ? `${wait[0]!.toUpperCase()}${wait.slice(1)}` : label.label}</span>
            {thread.reason && thread.status !== 'running' && thread.status !== 'waiting' ? ` · ${thread.reason}` : ''}
            {thread.review_round ? ` · revision round ${thread.review_round} of ${rounds}` : ''} · <span className="mono">
              {thread.model_override ?? thread.model}
              {thread.effort ? ` (${thread.effort} effort)` : ''}
            </span> · started {clock(thread.created_at)} ·{' '}
            {duration(now - Date.parse(thread.created_at))}
            {usage.length ? <> · {usage.map((u) => `${u.model.replace(/^claude-/, '')} ${tokens(u.prompt + u.completion)}`).join(' · ')}</> : null}
          </p>
          <div className="thread-chips">
            {answering ? <AnsweringBadge label={answering} /> : null}
            {thread.active_skills.map((sk) => (
              <SkillBadge key={sk} name={sk} />
            ))}
            <span className="chip chip-idle">{thread.git_branch ? <span className="mono">{thread.git_branch}</span> : 'Scratch workspace'}</span>
            {archived ? <span className="chip chip-idle">Archived</span> : null}
            {thread.status === 'done' ? (
              <Button size="sm" variant="ghost" pending={busy === 'skill'} onClick={() => void act('skill')}>
                Turn into a skill
              </Button>
            ) : null}
          </div>
        </div>
        <div className="tabs" role="tablist" aria-label="Thread">
          {TABS.map(([t, name]) => (
            <button key={t} type="button" role="tab" aria-selected={tab === t} onClick={() => setTab(t)}>
              {name}
              {t === 'drafts' && drafts.length ? <span className="tab-count">{drafts.length}</span> : null}
            </button>
          ))}
        </div>
        <div className="thread-tab" role="tabpanel">
          {tab === 'route' ? (
            stops.length ? (
              <RouteView stops={stops} running={thread.status === 'running'} activity={thread.activity} reviewRounds={rounds} messages={s.messages} selected={current} onSelect={setSelected} />
            ) : (
              <EmptyState title="Not started yet">The route draws itself as the thread works.</EmptyState>
            )
          ) : tab === 'result' ? (
            <ResultTab projectId={projectId} thread={thread} />
          ) : tab === 'diff' ? (
            <DiffTab threadId={thread.id} version={version} />
          ) : tab === 'files' ? (
            <FilesTab threadId={thread.id} dir={dir} onDir={setDir} version={version} />
          ) : tab === 'drafts' ? (
            <SkillDraftsTab
              projectId={projectId}
              threadTitle={thread.title ?? thread.id}
              drafts={drafts}
              onBrowse={(d) => {
                setDir(d);
                setTab('files');
              }}
            />
          ) : (
            <UsageTab usage={usage} />
          )}
        </div>
      </div>
      <Transcript
        projectId={projectId}
        threadId={thread.id}
        rows={rows}
        entries={transcript.entries}
        reviewRounds={rounds}
        messages={s.messages}
        selected={current}
        onSelect={setSelected}
        depth={depth}
        onDepth={setDepth}
        actions={actions}
        composer={composer}
      />
      {confirm === 'stop' ? (
        <ConfirmDialog title="Stop this thread?" confirmLabel="Stop thread" danger onConfirm={() => void act('stop')} onCancel={() => setConfirm(null)}>
          It stops at once, and pending approvals are denied. Its workspace and any branch are kept, and Desk is told.
        </ConfirmDialog>
      ) : null}
      {confirm === 'archive' ? (
        <ConfirmDialog title="Archive this thread?" confirmLabel="Archive" onConfirm={() => void act('archive')} onCancel={() => setConfirm(null)}>
          {thread.git_branch ? (
            <>
              The workspace is removed. The branch <span className="mono">{thread.git_branch}</span> is kept, so you can still merge it.
            </>
          ) : (
            'The scratch workspace is removed. Files it published stay in the Library.'
          )}
        </ConfirmDialog>
      ) : null}
    </div>
  );
}
