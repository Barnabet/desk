import { useEffect, useMemo, useState, type FormEvent } from 'react';
import type { AutomationDefinition, AutomationDetail, AutomationVersionInfo, Grant } from '@desk/protocol';
import {
  dayTime,
  describeGrant,
  diffDefinitions,
  GRANT_MATCH_LABEL,
  GRANT_TOOLS,
  GRANT_VALUE_LABEL,
  grantDraft,
  grantFromDraft,
  grantKey,
  grantOrigins,
  grantOriginText,
  href,
  originText,
  replaceGrant,
  widenedGrants,
  type GrantDraft,
  type GrantMatchKind,
} from '@desk/ui-core';
import { call } from '../../bridge';
import { Button } from '../../components/Button';
import { EmptyState } from '../../components/EmptyState';
import { Field } from '../../components/Field';
import { toast, toastError } from '../../components/Toast';
import { useNow } from '../../state/now';
import type { SessionState } from '../../state/session';
import { DiffView } from '../versions/DiffView';

type Props = { projectId: string; s: SessionState; detail: AutomationDetail; onChange(d: AutomationDetail): void };

/** Grants (spec §8.4): what this automation's runs may do without asking, where each rule came from, and the suspension banner. */
export function GrantsView(o: Props) {
  const d = o.detail;
  const now = useNow();
  const origins = useMemo(() => grantOrigins(o.s.events, d.id), [o.s.events, d.id]);
  /** The row being edited; `d.grants.length` is a new one. */
  const [editing, setEditing] = useState<number | null>(null);
  const [busy, setBusy] = useState(false);
  const save = async (grants: Grant[]) => {
    setBusy(true);
    try {
      o.onChange(await call('automations.setGrants', { id: d.id, grants, reason: 'edited' }));
      setEditing(null);
    } catch (err) {
      toastError(err);
    } finally {
      setBusy(false);
    }
  };
  return (
    <div className="auto-grants">
      {d.grants_suspended ? <SuspendedBanner detail={d} onChange={o.onChange} /> : null}
      <p className="muted small">Grants go before the project's policy, for this automation's scripts and step agents only.</p>
      {d.grants.length === 0 && editing === null ? (
        <EmptyState title="No grants">Its runs ask you for whatever the project's policy does not allow. Approve and remember adds a grant.</EmptyState>
      ) : null}
      <ul className="auto-grant-list">
        {d.grants.map((g, i) => {
          const origin = origins.get(grantKey(g));
          const wide = widenedGrants(g);
          return (
            <li key={grantKey(g)} className="auto-grant" aria-label={describeGrant(g)}>
              {editing === i ? (
                <GrantEditor initial={g} busy={busy} onSave={(next) => void save(replaceGrant(d.grants, i, [next]))} onCancel={() => setEditing(null)} />
              ) : (
                <>
                  <div className="grow">
                    <p className={`auto-grant-rule ${g.action}`}>{describeGrant(g)}</p>
                    <p className="muted small">
                      {grantOriginText(origin, d.definition, now)}
                      {origin?.kind === 'remembered' ? (
                        <>
                          {' · '}
                          <a className="link" href={href({ name: 'project', id: o.projectId, tab: 'automations', automationId: d.id, view: 'runs', runId: origin.run_id })}>
                            Open run
                          </a>
                        </>
                      ) : null}
                    </p>
                  </div>
                  {wide ? (
                    <Button size="sm" variant="ghost" disabled={busy} title={`Allow ${wide[0]?.match?.domain} and every subdomain of it, not only ${g.match?.domain}`} onClick={() => void save(replaceGrant(d.grants, i, wide))}>
                      {`Widen to ${wide[0]?.match?.domain}`}
                    </Button>
                  ) : null}
                  <Button size="sm" variant="ghost" disabled={busy} onClick={() => setEditing(i)}>
                    Edit
                  </Button>
                  <Button size="sm" variant="ghost" disabled={busy} onClick={() => void save(replaceGrant(d.grants, i, []))}>
                    Remove
                  </Button>
                </>
              )}
            </li>
          );
        })}
      </ul>
      {editing === d.grants.length ? (
        <GrantEditor busy={busy} onSave={(next) => void save(replaceGrant(d.grants, d.grants.length, [next]))} onCancel={() => setEditing(null)} />
      ) : (
        <div>
          <Button disabled={busy} onClick={() => setEditing(d.grants.length)}>
            Add grant
          </Button>
        </div>
      )}
    </div>
  );
}

function GrantEditor(o: { initial?: Grant; busy: boolean; onSave(g: Grant): void; onCancel(): void }) {
  const [draft, setDraft] = useState<GrantDraft>(() => grantDraft(o.initial));
  const [problem, setProblem] = useState<string | null>(null);
  const set = (patch: Partial<GrantDraft>) => {
    setDraft((x) => ({ ...x, ...patch }));
    setProblem(null);
  };
  const submit = (e: FormEvent) => {
    e.preventDefault();
    const r = grantFromDraft(draft);
    if ('problem' in r) setProblem(r.problem);
    else o.onSave(r.grant);
  };
  return (
    <form className="auto-grant-form" aria-label={o.initial ? 'Edit grant' : 'New grant'} onSubmit={submit}>
      <div className="auto-inline">
        <Field id="grant-action" label="Action">
          <select id="grant-action" className="select" value={draft.action} onChange={(e) => set({ action: e.target.value as Grant['action'] })}>
            <option value="allow">Allow</option>
            <option value="deny">Deny</option>
          </select>
        </Field>
        <Field id="grant-tool" label="Tool">
          <input id="grant-tool" className="input mono" list="grant-tools" maxLength={60} value={draft.tool} onChange={(e) => set({ tool: e.target.value })} />
        </Field>
        <datalist id="grant-tools">
          {GRANT_TOOLS.map((t) => (
            <option key={t} value={t} />
          ))}
        </datalist>
        <Field id="grant-kind" label="Matches">
          <select id="grant-kind" className="select" value={draft.kind} onChange={(e) => set({ kind: e.target.value as GrantMatchKind })}>
            {(Object.keys(GRANT_MATCH_LABEL) as GrantMatchKind[]).map((k) => (
              <option key={k} value={k}>
                {GRANT_MATCH_LABEL[k]}
              </option>
            ))}
          </select>
        </Field>
      </div>
      {draft.kind !== 'any' ? (
        <Field id="grant-value" label={GRANT_VALUE_LABEL[draft.kind]}>
          <input id="grant-value" className="input mono" value={draft.value} onChange={(e) => set({ value: e.target.value })} />
        </Field>
      ) : null}
      {problem ? (
        <p className="field-error" role="alert">
          {problem}
        </p>
      ) : null}
      <div className="auto-inline">
        <Button type="submit" variant="primary" pending={o.busy}>
          Save grant
        </Button>
        <Button onClick={o.onCancel}>Cancel</Button>
      </div>
    </form>
  );
}

function SuspendedBanner({ detail: d, onChange }: { detail: AutomationDetail; onChange(d: AutomationDetail): void }) {
  const now = useNow();
  const since = d.grants_set_version;
  const [versions, setVersions] = useState<AutomationVersionInfo[] | null>(null);
  const [before, setBefore] = useState<AutomationDefinition | null>(null);
  const [keeping, setKeeping] = useState(false);
  useEffect(() => {
    let live = true;
    call('automations.versions', { id: d.id })
      .then((list) => live && setVersions(list))
      .catch(toastError);
    if (since !== null) {
      call('automations.version', { id: d.id, version: since })
        .then((v) => live && setBefore(v.definition))
        .catch(toastError);
    }
    return () => {
      live = false;
    };
  }, [d.id, d.version, since]);
  const saved = (versions ?? []).filter((v) => since === null || v.version > since);
  const diff = useMemo(() => (before ? diffDefinitions(before, d.definition) : null), [before, d.definition]);
  const keep = async () => {
    setKeeping(true);
    try {
      onChange(await call('automations.keepGrants', { id: d.id }));
      toast({ tone: 'info', message: `Grants kept for v${d.version}.` });
    } catch (err) {
      toastError(err);
    } finally {
      setKeeping(false);
    }
  };
  return (
    <section className="auto-banner" aria-label="Grants suspended">
      <p>
        <b>Grants are suspended.</b>
        {` v${d.version} was saved after they were set${since !== null ? ` in v${since}` : ''}, so its runs ask you for everything and approvals offer no remember until you keep them. Editing a grant keeps them too.`}
      </p>
      {saved.length ? (
        <ul className="auto-plain">
          {saved.map((v) => (
            <li key={v.version}>
              <b>{`v${v.version}`}</b> <span className="muted small">{`${originText(v)} · ${dayTime(v.created_at, now)}`}</span>
              {v.change_note ? <p>{v.change_note}</p> : null}
            </li>
          ))}
        </ul>
      ) : null}
      {since !== null ? (
        <section aria-label={`What changed since v${since}`}>
          {diff ? <DiffView diff={diff} labels={{ before: `v${since}`, after: `v${d.version}` }} /> : <p className="muted">Loading…</p>}
        </section>
      ) : null}
      <div>
        <Button variant="primary" pending={keeping} onClick={() => void keep()}>
          Keep grants
        </Button>
      </div>
    </section>
  );
}
