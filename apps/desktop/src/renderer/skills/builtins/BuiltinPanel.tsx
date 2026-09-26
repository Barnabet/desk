import { useEffect, useState } from 'react';
import type { SkillDetail } from '@desk/client';
import type { BuiltinSkillInfo } from '@desk/protocol';
import { call } from '../../bridge';
import { Button } from '../../components/Button';
import { EmptyState } from '../../components/EmptyState';
import { Field } from '../../components/Field';
import { FileViewer } from '../../components/FileViewer';
import { SafeMarkdown } from '../../components/SafeMarkdown';
import { Sheet } from '../../components/Sheet';
import { describeError, toast, toastError } from '../../components/Toast';
import { bytes } from '../../format';
import { href } from '../../router';
import type { SkillRef } from '../data';
import { BuiltinRuntime, BuiltinSwitch } from './BuiltinGroup';

type Tab = 'overview' | 'instructions' | 'files';

function DuplicateSheet(o: { item: BuiltinSkillInfo; projects: Array<{ id: string; name: string }>; onDone(ref: SkillRef): void; onClose(): void }) {
  const [target, setTarget] = useState('global');
  const [pending, setPending] = useState(false);
  const run = async () => {
    setPending(true);
    try {
      const projectId = target === 'global' ? undefined : target;
      await call('builtins.duplicate', { name: o.item.name, ...(projectId ? { projectId } : {}) });
      toast({ tone: 'info', message: `Duplicated ${o.item.name}. Your copy is used instead of the built-in.` });
      o.onDone(projectId ? { scope: 'project', projectId, name: o.item.name } : { scope: 'global', name: o.item.name });
    } catch (err) {
      toastError(err);
    } finally {
      setPending(false);
    }
  };
  return (
    <Sheet
      title={`Duplicate ${o.item.name}`}
      onClose={o.onClose}
      footer={
        <>
          <Button onClick={o.onClose}>Cancel</Button>
          <Button variant="primary" pending={pending} onClick={() => void run()}>
            Duplicate
          </Button>
        </>
      }
    >
      <p className="small">
        Your copy is an ordinary skill you can edit. Agents use it instead of the built-in until you delete it; its scripts keep using the built-in's Python environment.
      </p>
      <Field id="duplicate-scope" label="Where">
        <select id="duplicate-scope" className="select" value={target} onChange={(e) => setTarget(e.target.value)}>
          <option value="global">My skills (every project)</option>
          {o.projects.map((p) => (
            <option key={p.id} value={p.id}>
              {p.name} only
            </option>
          ))}
        </select>
      </Field>
    </Sheet>
  );
}

/** One of Desk's built-in skills: read-only instructions and files, its environment, the switch, and Duplicate. */
export function BuiltinPanel(o: { item: BuiltinSkillInfo; projects: Array<{ id: string; name: string }>; onDuplicated(ref: SkillRef): void; onChanged(): void; onClose(): void }) {
  const { item } = o;
  const [tab, setTab] = useState<Tab>('overview');
  const [detail, setDetail] = useState<SkillDetail | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [file, setFile] = useState<{ path: string; data: Uint8Array } | null>(null);
  const [dup, setDup] = useState(false);
  const [retrying, setRetrying] = useState(false);

  useEffect(() => {
    let live = true;
    setTab('overview');
    setFile(null);
    setError(null);
    call('builtins.get', { name: item.name })
      .then((d) => live && setDetail(d))
      .catch((err) => live && setError(describeError(err).message));
    return () => {
      live = false;
    };
  }, [item.name]);

  const openFile = async (path: string) => {
    try {
      setFile({ path, data: await call('builtins.file', { name: item.name, path }) });
    } catch (err) {
      toastError(err);
    }
  };
  const retry = async () => {
    setRetrying(true);
    try {
      await call('builtins.retry', { name: item.name });
      toast({ tone: 'info', message: `Setting up ${item.name} again.` });
      o.onChanged();
    } catch (err) {
      toastError(err);
    } finally {
      setRetrying(false);
    }
  };

  return (
    <article className="card skill-panel" aria-label={`Built-in skill ${item.name}`}>
      <div className="skill-panel-head">
        <h2 className="mono">{item.name}</h2>
        <span className="skill-scope builtin">Built in</span>
        {item.broken ? null : <BuiltinSwitch item={item} label={`${item.title} on`} onChanged={o.onChanged} />}
        <button type="button" className="icon-btn" aria-label="Close" onClick={o.onClose}>
          ✕
        </button>
      </div>
      <p className="skill-desc">{item.summary}</p>
      {item.broken ? (
        <p className="field-error" role="alert">
          This copy of Desk is damaged ({item.broken}), so agents can't use it. Reinstall Desk to repair it.
        </p>
      ) : (
        <div className="builtin-runtime">
          <BuiltinRuntime item={item} />
          {item.runtime.state === 'failed' ? (
            <Button size="sm" pending={retrying} onClick={() => void retry()}>
              Retry
            </Button>
          ) : null}
        </div>
      )}
      {item.enabled ? null : <p className="shadow-note">Turned off: agents never see it. Turn it back on with the switch.</p>}
      {item.shadowed_by === 'global' ? (
        <p className="shadow-note">
          Shadowed by your skill <a href={href({ name: 'skills', skill: `global:${item.name}` })}>{item.name}</a>: agents use yours. Delete it to bring the built-in back.
        </p>
      ) : null}
      {error ? (
        <EmptyState title="Couldn't load this skill">{error}</EmptyState>
      ) : !detail ? (
        <p className="muted">Loading…</p>
      ) : (
        <>
          <div className="tabs" role="tablist" aria-label="Skill">
            {(['overview', 'instructions', 'files'] as Tab[]).map((t) => (
              <button key={t} type="button" role="tab" aria-selected={tab === t} onClick={() => setTab(t)}>
                {t === 'overview' ? 'Overview' : t === 'instructions' ? 'Instructions' : `Files · ${detail.files.length}`}
              </button>
            ))}
          </div>
          <div className="skill-tab" role="tabpanel">
            {tab === 'overview' ? (
              <>
                <p className="small">{detail.description}</p>
                {item.caveats.length ? (
                  <div>
                    <span className="label">Good to know</span>
                    <ul className="builtin-caveats">
                      {item.caveats.map((c) => (
                        <li key={c} className="small">
                          {c}
                        </li>
                      ))}
                    </ul>
                  </div>
                ) : null}
                <p className="muted small">
                  {item.scripts} scripts. Desk sets up their Python environment the first time an agent uses the skill, and updates it with Desk.
                </p>
              </>
            ) : tab === 'instructions' ? (
              <SafeMarkdown text={detail.instructions} />
            ) : (
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
            )}
          </div>
        </>
      )}
      <div className="skill-actions">
        <Button variant="primary" onClick={() => setDup(true)}>
          Duplicate to my skills
        </Button>
        <span className="grow" />
      </div>
      {dup ? (
        <DuplicateSheet
          item={item}
          projects={o.projects}
          onClose={() => setDup(false)}
          onDone={(ref) => {
            setDup(false);
            o.onDuplicated(ref);
          }}
        />
      ) : null}
    </article>
  );
}
