import { useEffect, useMemo, useState } from 'react';
import type { AutomationDefinition, AutomationDetail, AutomationVersionInfo } from '@desk/protocol';
import { dayTime, diffDefinitions, originText } from '@desk/ui-core';
import { call } from '../../bridge';
import { Button } from '../../components/Button';
import { ConfirmDialog } from '../../components/ConfirmDialog';
import { EmptyState } from '../../components/EmptyState';
import { toast, toastError } from '../../components/Toast';
import { useNow } from '../../state/now';
import { DiffView } from './DiffView';

/** Loads version definitions once each. */
function useDefinitions(id: string, versions: number[]): Record<number, AutomationDefinition> {
  const [defs, setDefs] = useState<Record<number, AutomationDefinition>>({});
  const want = versions.filter((v) => v > 0 && !defs[v]).join(',');
  useEffect(() => {
    if (!want) return;
    let live = true;
    for (const v of want.split(',').map(Number)) {
      call('automations.version', { id, version: v })
        .then((r) => live && setDefs((d) => ({ ...d, [v]: r.definition })))
        .catch(toastError);
    }
    return () => {
      live = false;
    };
  }, [id, want]);
  return defs;
}

/** Versions (spec §8.4): who saved each and when, its note and test, a diff between any two, and Restore. */
export function VersionsView(o: { detail: AutomationDetail; onChange(d: AutomationDetail): void }) {
  const d = o.detail;
  const now = useNow();
  const [versions, setVersions] = useState<AutomationVersionInfo[] | null>(null);
  const [from, setFrom] = useState(0);
  const [to, setTo] = useState(0);
  const [restoring, setRestoring] = useState<number | null>(null);
  useEffect(() => {
    let live = true;
    call('automations.versions', { id: d.id })
      .then((list) => {
        if (!live) return;
        setVersions(list);
        setTo(list[0]?.version ?? 0);
        setFrom(list[1]?.version ?? list[0]?.version ?? 0);
      })
      .catch(toastError);
    return () => {
      live = false;
    };
  }, [d.id, d.version]);
  const defs = useDefinitions(d.id, [from, to]);
  const diff = useMemo(() => (defs[from] && defs[to] ? diffDefinitions(defs[from]!, defs[to]!) : null), [defs, from, to]);
  const restore = async (version: number) => {
    setRestoring(null);
    try {
      const next = await call('automations.restore', { id: d.id, version });
      toast({ tone: 'info', message: `Restored v${version} as v${next.version}.` });
      o.onChange(next);
    } catch (err) {
      toastError(err);
    }
  };
  if (!versions) return <p className="muted auto-loading">Loading…</p>;
  if (!versions.length) return <EmptyState title="No versions">Saving the automation creates its first version.</EmptyState>;
  const pick = (id: string, label: string, value: number, set: (v: number) => void) => (
    <label className="auto-inline small" htmlFor={id}>
      {label}
      <select id={id} className="select" value={String(value)} onChange={(e) => set(Number(e.target.value))}>
        {versions.map((v) => (
          <option key={v.version} value={String(v.version)}>{`v${v.version}`}</option>
        ))}
      </select>
    </label>
  );
  return (
    <div className="auto-versions">
      <ul className="auto-version-list">
        {versions.map((v) => (
          <li key={v.version} className="auto-version">
            <div className="auto-version-head">
              <b className="mono">{`v${v.version}`}</b>
              <span>{originText(v)}</span>
              <span className="muted small">{dayTime(v.created_at, now)}</span>
              {v.tested ? <span className="auto-tag added">✓ tested</span> : null}
              {v.version === d.version ? <span className="auto-tag">current</span> : null}
              <span className="grow" />
              {v.version !== d.version ? (
                <Button size="sm" variant="ghost" onClick={() => setRestoring(v.version)}>
                  Restore…
                </Button>
              ) : null}
            </div>
            {v.change_note ? <p className="auto-version-note">{v.change_note}</p> : null}
          </li>
        ))}
      </ul>
      <section className="auto-version-diff" aria-label={`Changes from v${from} to v${to}`}>
        <div className="auto-inline">
          {pick('versions-from', 'Compare', from, setFrom)}
          {pick('versions-to', 'with', to, setTo)}
        </div>
        {diff ? <DiffView diff={diff} labels={{ before: `v${from}`, after: `v${to}` }} /> : <p className="muted">Loading…</p>}
      </section>
      {restoring !== null ? (
        <ConfirmDialog title={`Restore v${restoring}?`} confirmLabel="Restore" onConfirm={() => void restore(restoring)} onCancel={() => setRestoring(null)}>
          {`Its definition becomes a new version, v${d.version + 1}. Nothing is lost: v${d.version} stays in the list.`}
        </ConfirmDialog>
      ) : null}
    </div>
  );
}
