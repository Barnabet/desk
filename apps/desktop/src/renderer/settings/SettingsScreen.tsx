import { useEffect, useState } from 'react';
import type { PolicyRule } from '@desk/protocol';
import { call } from '../bridge';
import { Button } from '../components/Button';
import { ConfirmDialog } from '../components/ConfirmDialog';
import { EmptyState } from '../components/EmptyState';
import { Field } from '../components/Field';
import { toast, toastError } from '../components/Toast';
import { navigate } from '../router';
import { useSession } from '../state/session';
import { PolicyEditor, sameRules } from './PolicyEditor';
import { SettingsFields, workingStyleOf, type WorkingStyle } from './SettingsFields';
import { useModels } from './useModels';
import './settings.css';

function useSaver() {
  const [busy, setBusy] = useState<string | null>(null);
  const run = async (what: string, fn: () => Promise<unknown>, done?: string) => {
    setBusy(what);
    try {
      await fn();
      if (done) toast({ tone: 'info', message: done });
      return true;
    } catch (err) {
      toastError(err);
      return false;
    } finally {
      setBusy(null);
    }
  };
  return { busy, run };
}

/** A project's settings: what it is, where its sources are, how Desk works, the policy, and archiving. */
export function SettingsScreen({ projectId }: { projectId: string }) {
  const s = useSession(projectId);
  const models = useModels();
  const project = s.project?.project;
  const { busy, run } = useSaver();
  const [about, setAbout] = useState<{ name: string; goal: string; instructions: string } | null>(null);
  const [style, setStyle] = useState<WorkingStyle | null>(null);
  const [policy, setPolicy] = useState<PolicyRule[] | null>(null);
  const [confirmArchive, setConfirmArchive] = useState(false);

  // Drafts start from the live project and reset when it changes underneath (after a save, or another client).
  const settingsKey = JSON.stringify(project?.settings ?? null);
  useEffect(() => {
    if (!project) return;
    setAbout({ name: project.name, goal: project.goal, instructions: project.instructions });
  }, [project?.name, project?.goal, project?.instructions]);
  useEffect(() => {
    if (!project) return;
    setStyle(workingStyleOf(project.settings));
    setPolicy(project.settings.policy);
  }, [settingsKey]);

  if (s.status === 'loading' || !about || !style || !policy) return <div className="page muted">Loading…</div>;
  if (s.status !== 'ready' || !project || !s.project)
    return (
      <div className="page">
        <EmptyState title="Couldn't load this project">{s.error}</EmptyState>
      </div>
    );

  const aboutDirty = about.name !== project.name || about.goal !== project.goal || about.instructions !== project.instructions;
  const styleDirty = JSON.stringify(style) !== JSON.stringify(workingStyleOf(project.settings));
  const policyDirty = !sameRules(policy, project.settings.policy);

  const addSource = async () => {
    const path = await call('app.pickFolder', { purpose: 'source' }).catch((err) => (toastError(err), null));
    if (path) await run('source', () => call('projects.addSource', { id: projectId, source: { path } }));
  };

  return (
    <div className="page settings">
      <h1 className="title">Settings</h1>

      <section className="card settings-section" aria-labelledby="set-about">
        <h2 id="set-about">About this project</h2>
        <Field id="set-name" label="Name">
          <input id="set-name" className="input" value={about.name} onChange={(e) => setAbout({ ...about, name: e.target.value })} />
        </Field>
        <Field id="set-goal" label="Goal">
          <textarea id="set-goal" className="textarea" value={about.goal} onChange={(e) => setAbout({ ...about, goal: e.target.value })} />
        </Field>
        <Field id="set-instructions" label="Standing instructions" hint="Desk and every thread read these before they start.">
          <textarea id="set-instructions" className="textarea" rows={4} value={about.instructions} onChange={(e) => setAbout({ ...about, instructions: e.target.value })} />
        </Field>
        <div className="actions">
          <Button
            variant="primary"
            pending={busy === 'about'}
            disabled={!aboutDirty || !about.name.trim()}
            onClick={() => void run('about', () => call('projects.update', { id: projectId, patch: { name: about.name.trim(), goal: about.goal, instructions: about.instructions } }), 'Saved.')}
          >
            Save
          </Button>
        </div>
      </section>

      <section className="card settings-section" aria-labelledby="set-sources">
        <h2 id="set-sources">Sources</h2>
        <p className="field-hint">Folders Desk and its threads can read. A git repository gets its own branch per thread; Desk never merges.</p>
        {s.project.sources.length ? (
          <ul className="sources">
            {s.project.sources.map((src) => (
              <li key={src.id}>
                <span className={`chip ${src.kind === 'git' ? 'chip-run' : 'chip-idle'}`}>{src.kind}</span>
                <span className="grow">
                  <strong>{src.label}</strong> <span className="mono small muted">{src.path}</span>
                </span>
                <Button size="sm" variant="ghost" aria-label={`Remove ${src.label}`} pending={busy === `rm-${src.id}`} onClick={() => void run(`rm-${src.id}`, () => call('projects.removeSource', { id: projectId, sourceId: src.id }))}>
                  Remove
                </Button>
              </li>
            ))}
          </ul>
        ) : (
          <p className="muted">No sources yet.</p>
        )}
        <div>
          <Button size="sm" pending={busy === 'source'} onClick={() => void addSource()}>
            Add folder…
          </Button>
        </div>
      </section>

      <section className="card settings-section" aria-labelledby="set-style">
        <h2 id="set-style">How Desk works</h2>
        <SettingsFields value={style} models={models} onChange={(p) => setStyle({ ...style, ...p })} />
        <div className="actions">
          <Button variant="primary" pending={busy === 'style'} disabled={!styleDirty} onClick={() => void run('style', () => call('projects.update', { id: projectId, patch: { settings: style } }), 'Saved.')}>
            Save
          </Button>
          {styleDirty ? (
            <Button variant="ghost" onClick={() => setStyle(workingStyleOf(project.settings))}>
              Discard changes
            </Button>
          ) : null}
        </div>
      </section>

      <section className="card settings-section" aria-labelledby="set-policy">
        <h2 id="set-policy">Policy</h2>
        <PolicyEditor rules={policy} onChange={setPolicy} />
        <div className="actions">
          <Button
            variant="primary"
            pending={busy === 'policy'}
            disabled={!policyDirty || policy.some((r) => !r.tool.trim())}
            onClick={() => void run('policy', () => call('projects.update', { id: projectId, patch: { settings: { policy } } }), 'Policy saved.')}
          >
            Save policy
          </Button>
          {policyDirty ? (
            <Button variant="ghost" onClick={() => setPolicy(project.settings.policy)}>
              Discard changes
            </Button>
          ) : null}
        </div>
      </section>

      <section className="card settings-section danger" aria-labelledby="set-archive">
        <h2 id="set-archive">Archive</h2>
        <p className="small">Archiving stops the project's threads and hides it from the map. Its library, memory and branches are kept.</p>
        <div>
          <Button variant="danger" pending={busy === 'archive'} onClick={() => setConfirmArchive(true)}>
            Archive project…
          </Button>
        </div>
      </section>
      {confirmArchive ? (
        <ConfirmDialog
          title={`Archive ${project.name}?`}
          confirmLabel="Archive"
          danger
          onCancel={() => setConfirmArchive(false)}
          onConfirm={() => {
            setConfirmArchive(false);
            void run('archive', () => call('projects.archive', { id: projectId })).then((ok) => ok && navigate({ name: 'map' }));
          }}
        >
          Running threads are stopped. Nothing is deleted.
        </ConfirmDialog>
      ) : null}
    </div>
  );
}
