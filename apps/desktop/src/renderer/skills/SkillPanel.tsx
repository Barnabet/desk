import { useEffect, useMemo, useState } from 'react';
import type { SkillDetail, SkillHistoryEntry, SkillNode } from '@desk/client';
import { call } from '../bridge';
import { Button } from '../components/Button';
import { ConfirmDialog } from '../components/ConfirmDialog';
import { EmptyState } from '../components/EmptyState';
import { FileViewer } from '../components/FileViewer';
import { SafeMarkdown } from '../components/SafeMarkdown';
import { describeError, toast, toastError } from '../components/Toast';
import { ago, bytes } from '../format';
import { href } from '../router';
import { useNow } from '../state/now';
import { scopeArg, whoLabel, type SkillRef } from './data';
import { diffFiles, diffLines, withContext } from './diff';

type Tab = 'overview' | 'instructions' | 'files' | 'history';

function Compare({ skill, history }: { skill: SkillRef; history: SkillHistoryEntry[] }) {
  const versions = history.map((h) => h.version);
  const [from, setFrom] = useState(versions.at(-2) ?? versions[0] ?? 1);
  const [to, setTo] = useState(versions.at(-1) ?? 1);
  const [pair, setPair] = useState<[SkillDetail, SkillDetail] | null>(null);
  const [error, setError] = useState<string | null>(null);
  useEffect(() => {
    let live = true;
    setPair(null);
    Promise.all([call('skills.version', { ...scopeArg(skill), name: skill.name, version: from }), call('skills.version', { ...scopeArg(skill), name: skill.name, version: to })])
      .then(([a, b]) => live && (setPair([a, b]), setError(null)))
      .catch((err) => live && setError(describeError(err).message));
    return () => {
      live = false;
    };
  }, [skill.scope, skill.projectId, skill.name, from, to]);
  const lines = useMemo(() => (pair ? withContext(diffLines(`${pair[0].description}\n\n${pair[0].instructions}`, `${pair[1].description}\n\n${pair[1].instructions}`)) : []), [pair]);
  const files = useMemo(() => (pair ? diffFiles(pair[0].files, pair[1].files).filter((f) => f.change !== 'same') : []), [pair]);
  return (
    <div className="skill-compare">
      <div className="skill-compare-bar">
        <label>
          Compare{' '}
          <select className="select" aria-label="From version" value={from} onChange={(e) => setFrom(Number(e.target.value))}>
            {versions.map((v) => (
              <option key={v} value={v}>
                v{v}
              </option>
            ))}
          </select>
        </label>
        <label>
          with{' '}
          <select className="select" aria-label="To version" value={to} onChange={(e) => setTo(Number(e.target.value))}>
            {versions.map((v) => (
              <option key={v} value={v}>
                v{v}
              </option>
            ))}
          </select>
        </label>
      </div>
      {error ? <p className="field-error">{error}</p> : null}
      {pair ? (
        <>
          {lines.length ? (
            <pre className="diff-patch" aria-label={`Changes from v${from} to v${to}`}>
              {lines.map((l, i) =>
                l === null ? (
                  <span key={i} className="diff-hunk">
                    {'…\n'}
                  </span>
                ) : (
                  <span key={i} className={l.kind === 'add' ? 'diff-line-add' : l.kind === 'del' ? 'diff-line-del' : undefined}>
                    {l.kind === 'add' ? '+ ' : l.kind === 'del' ? '- ' : '  '}
                    {l.text}
                    {'\n'}
                  </span>
                ),
              )}
            </pre>
          ) : (
            <p className="muted small">The instructions are the same.</p>
          )}
          {files.length ? (
            <ul className="skill-file-changes">
              {files.map((f) => (
                <li key={f.path}>
                  <span className={`chip ${f.change === 'added' ? 'chip-run' : f.change === 'removed' ? 'chip-fail' : 'chip-idle'}`}>{f.change}</span> <span className="mono">{f.path}</span>
                  {f.change === 'changed' ? <span className="muted small"> {bytes(f.before!)} → {bytes(f.after!)}</span> : null}
                </li>
              ))}
            </ul>
          ) : null}
        </>
      ) : error ? null : (
        <p className="muted small">Loading…</p>
      )}
    </div>
  );
}

/** One skill in full: overview, instructions, files and history (compare any two versions, restore), with edit, delete and Desk. */
export function SkillPanel(o: {
  skill: SkillRef;
  node: SkillNode | undefined;
  projectNames: Map<string, string>;
  threadTitles: Map<string, string>;
  version: number;
  onEdit(detail: SkillDetail): void;
  onAskDesk(): void;
  onChanged(): void;
  onClose(): void;
}) {
  const { skill } = o;
  const now = useNow();
  const [tab, setTab] = useState<Tab>('overview');
  const [detail, setDetail] = useState<SkillDetail | null>(null);
  const [history, setHistory] = useState<SkillHistoryEntry[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [file, setFile] = useState<{ path: string; data: Uint8Array } | null>(null);
  const [confirm, setConfirm] = useState<{ kind: 'delete' } | { kind: 'restore'; version: number } | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [reload, setReload] = useState(0);

  useEffect(() => {
    let live = true;
    setError(null);
    Promise.all([call('skills.get', { ...scopeArg(skill), name: skill.name }), call('skills.history', { ...scopeArg(skill), name: skill.name })])
      .then(([d, h]) => live && (setDetail(d), setHistory(h)))
      .catch((err) => live && setError(describeError(err).message));
    return () => {
      live = false;
    };
  }, [skill.scope, skill.projectId, skill.name, o.version, reload]);
  useEffect(() => {
    setTab('overview');
    setFile(null);
  }, [skill.scope, skill.projectId, skill.name]);

  const openFile = async (path: string) => {
    try {
      setFile({ path, data: await call('skills.file', { ...scopeArg(skill), name: skill.name, path }) });
    } catch (err) {
      toastError(err);
    }
  };
  const act = async (what: string, fn: () => Promise<unknown>, done: string) => {
    setConfirm(null);
    setBusy(what);
    try {
      await fn();
      toast({ tone: 'info', message: done });
      o.onChanged();
      setReload((r) => r + 1);
      return true;
    } catch (err) {
      toastError(err);
      return false;
    } finally {
      setBusy(null);
    }
  };

  const node = o.node;
  const current = history.find((h) => h.current);
  const scopeLabel = skill.scope === 'global' ? 'Global' : (o.projectNames.get(skill.projectId!) ?? 'Project');
  return (
    <article className="card skill-panel" aria-label={`Skill ${skill.name}`}>
      <div className="skill-panel-head">
        <h2 className="mono">{skill.name}</h2>
        <span className={`skill-scope ${skill.scope}`}>{scopeLabel}</span>
        <button type="button" className="icon-btn" aria-label="Close" onClick={o.onClose}>
          ✕
        </button>
      </div>
      {error ? (
        <EmptyState title="Couldn't load this skill">{error}</EmptyState>
      ) : !detail ? (
        <p className="muted">Loading…</p>
      ) : (
        <>
          <p className="skill-desc">{detail.description}</p>
          {detail.error ? (
            <p className="field-error" role="alert">
              SKILL.md has a problem, so agents can't use this skill until it's fixed: {detail.error}
            </p>
          ) : null}
          <div className="tabs" role="tablist" aria-label="Skill">
            {(['overview', 'instructions', 'files', 'history'] as Tab[]).map((t) => (
              <button key={t} type="button" role="tab" aria-selected={tab === t} onClick={() => setTab(t)}>
                {t === 'overview' ? 'Overview' : t === 'instructions' ? 'Instructions' : t === 'files' ? `Files · ${detail.files.length}` : `History · ${history.length}`}
              </button>
            ))}
          </div>
          <div className="skill-tab" role="tabpanel">
            {tab === 'overview' ? (
              <>
                {node?.shadows ? (
                  <p className="shadow-note">
                    {scopeLabel} has its own {skill.name}, so agents there use this one instead of the global skill.
                  </p>
                ) : null}
                {node?.shadowedIn.length ? (
                  <p className="shadow-note">
                    {node.shadowedIn.map((id) => o.projectNames.get(id) ?? id).join(', ')} {node.shadowedIn.length === 1 ? 'has' : 'have'} its own {skill.name}, so agents there use that one. Everywhere
                    else they use this.
                  </p>
                ) : null}
                {current ? (
                  <div className="skill-change">
                    <strong className="small">
                      {whoLabel(current.origin, o.threadTitles)} · v{current.version}
                      {current.ts ? ` · ${ago(current.ts, now)} ago` : ''}
                    </strong>
                    <span className="small">{current.change_note || 'No change note.'}</span>
                  </div>
                ) : null}
                <div className="skill-versions" role="group" aria-label="Versions">
                  {history
                    .slice()
                    .reverse()
                    .map((h) => (
                      <button key={h.version} type="button" className={h.current ? 'on' : undefined} aria-pressed={h.current} onClick={() => setTab('history')}>
                        v{h.version}
                      </button>
                    ))}
                </div>
                <div>
                  <span className="label">Used by</span>
                  {node?.usedBy.length ? (
                    <ul className="skill-used">
                      {node.usedBy.map((u) => (
                        <li key={u.threadId}>
                          <span className={`status-dot status-dot-${u.status}`} aria-hidden="true" />
                          <a href={href({ name: 'project', id: u.projectId, tab: 'threads', threadId: u.threadId })}>{u.title ?? 'Thread'}</a>
                          <span className="muted small"> · {u.status} · {o.projectNames.get(u.projectId) ?? ''}</span>
                        </li>
                      ))}
                    </ul>
                  ) : (
                    <p className="muted small">Not in use right now.</p>
                  )}
                </div>
              </>
            ) : tab === 'instructions' ? (
              <SafeMarkdown text={detail.instructions} />
            ) : tab === 'files' ? (
              <div className="skill-files">
                <ul className="files-list">
                  {detail.files.map((f) => (
                    <li key={f.path}>
                      <button type="button" className={`files-entry${file?.path === f.path ? ' current' : ''}`} onClick={() => void openFile(f.path)}>
                        <span className="grow mono">{f.path}</span>
                        <span className="muted small">{bytes(f.size)}</span>
                      </button>
                    </li>
                  ))}
                </ul>
                {file ? <FileViewer path={file.path} data={file.data} /> : <p className="muted small">Pick a file to view it.</p>}
              </div>
            ) : (
              <>
                <ol className="skill-history" reversed>
                  {history
                    .slice()
                    .reverse()
                    .map((h) => (
                      <li key={h.version}>
                        <span className="mono">v{h.version}</span>
                        <span className="grow">
                          <span className="small">
                            <strong>{whoLabel(h.origin, o.threadTitles)}</strong>
                            {h.ts ? ` · ${ago(h.ts, now)} ago` : ''}
                          </span>
                          <span className="small muted">{h.change_note || h.description}</span>
                        </span>
                        {h.current ? (
                          <span className="chip chip-done">current</span>
                        ) : (
                          <Button size="sm" pending={busy === `restore-${h.version}`} onClick={() => setConfirm({ kind: 'restore', version: h.version })}>
                            Restore
                          </Button>
                        )}
                      </li>
                    ))}
                </ol>
                {history.length > 1 ? <Compare skill={skill} history={history} /> : null}
              </>
            )}
          </div>
          <div className="skill-actions">
            <Button variant="primary" onClick={() => o.onEdit(detail)}>
              Edit
            </Button>
            <Button onClick={o.onAskDesk}>Refine with Desk</Button>
            <span className="grow" />
            <Button variant="ghost" pending={busy === 'delete'} onClick={() => setConfirm({ kind: 'delete' })}>
              Delete
            </Button>
          </div>
        </>
      )}
      {confirm?.kind === 'restore' ? (
        <ConfirmDialog
          title={`Restore v${confirm.version}?`}
          confirmLabel="Restore"
          onCancel={() => setConfirm(null)}
          onConfirm={() => void act(`restore-${confirm.version}`, () => call('skills.restore', { ...scopeArg(skill), name: skill.name, version: confirm.version }), `Restored v${confirm.version} as a new version.`)}
        >
          It comes back as the newest version. Nothing in the history is lost.
        </ConfirmDialog>
      ) : null}
      {confirm?.kind === 'delete' ? (
        <ConfirmDialog
          title={`Delete ${skill.name}?`}
          confirmLabel="Delete"
          danger
          onCancel={() => setConfirm(null)}
          onConfirm={() =>
            void act('delete', () => call('skills.remove', { ...scopeArg(skill), name: skill.name }), `Deleted ${skill.name}.`).then((ok) => ok && o.onClose())
          }
        >
          Agents stop using it. Its past versions stay in the skill's history on disk.
        </ConfirmDialog>
      ) : null}
    </article>
  );
}
