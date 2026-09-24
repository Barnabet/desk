import { useEffect, useMemo, useState } from 'react';
import type { CatalogItem, CatalogReview, ReviewWarningKind } from '@desk/protocol';
import { call } from '../../bridge';
import { Button } from '../../components/Button';
import { ExternalLink } from '../../components/ExternalLink';
import { Field } from '../../components/Field';
import { FileViewer } from '../../components/FileViewer';
import { Sheet } from '../../components/Sheet';
import { describeError, toast, toastError } from '../../components/Toast';
import { bytes } from '../../format';
import { navigate } from '../../router';
import { skillKey, type SkillRef } from '../data';
import { actionFor, installRef, runtimePackages, runtimeWords, sourceLabel } from './data';
import { RuntimeLine } from './RuntimeLine';

const WARNING: Record<ReviewWarningKind, string> = {
  'exec-block': 'A command block. Some agents run these before reading the skill; Desk never does.',
  'pipe-to-shell': 'Downloads a script and runs it.',
  'base64-blob': 'A long run of encoded data.',
  'invisible-unicode': 'Invisible characters, which can hide text.',
  'paste-site': 'A link to a paste site.',
  'memory-write': 'Asks the agent to write to memory or instruction files.',
};

const encoder = new TextEncoder();

/**
 * Everything to check before installing a catalog skill: the pinned source, the licence, every file (scripts
 * marked), anything worth a look, what Desk will set up, and where to install it. Then Install, with progress.
 */
export function ReviewSheet(o: { id: string; item: CatalogItem | undefined; projects: Array<{ id: string; name: string }>; onChanged(): void; onClose(): void }) {
  const [review, setReview] = useState<CatalogReview | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [file, setFile] = useState<{ path: string; data: Uint8Array; line?: number } | null>(null);
  const [scope, setScope] = useState('global');
  const [replace, setReplace] = useState(false);
  const [showLicense, setShowLicense] = useState(false);
  const [pending, setPending] = useState(false);
  const [installed, setInstalled] = useState<SkillRef | null>(null);

  useEffect(() => {
    let live = true;
    setReview(null);
    setError(null);
    call('catalog.prepare', { id: o.id })
      .then((r) => {
        if (!live) return;
        setReview(r);
        setFile({ path: 'SKILL.md', data: encoder.encode(r.skill_md) });
      })
      .catch((err) => live && setError(describeError(err).message));
    return () => {
      live = false;
    };
  }, [o.id]);

  const openFile = async (path: string, line?: number) => {
    if (path === 'SKILL.md' && review) return setFile({ path, data: encoder.encode(review.skill_md), ...(line ? { line } : {}) });
    try {
      setFile({ path, data: await call('catalog.file', { id: o.id, path }), ...(line ? { line } : {}) });
    } catch (err) {
      toastError(err);
    }
  };

  const projectId = scope === 'global' ? undefined : scope;
  const current = o.item?.installs.find((i) => (projectId ? i.scope === 'project' && i.project_id === projectId : i.scope === 'global'));
  const action = actionFor(current);
  const installedHere = installed ? o.item?.installs.find((i) => skillKey(installRef(o.id, i)) === skillKey(installed)) : undefined;
  const entry = review?.entry ?? o.item;
  const packages = useMemo(() => (entry ? runtimePackages(entry) : []), [entry]);

  const install = async () => {
    setPending(true);
    try {
      const r = await call('catalog.install', { id: o.id, ...(projectId ? { projectId } : {}), ...(action.kind === 'modified' ? { replaceModified: true } : {}) });
      const ref: SkillRef = r.skill.scope === 'global' ? { scope: 'global', name: r.skill.name } : { scope: 'project', projectId: r.skill.project_id!, name: r.skill.name };
      setInstalled(ref);
      toast({ tone: 'info', message: `${action.kind === 'update' ? 'Updated' : 'Installed'} ${r.skill.name}.` });
      o.onChanged();
    } catch (err) {
      toastError(err);
    } finally {
      setPending(false);
    }
  };

  const title = entry ? `${action.kind === 'update' ? 'Update' : 'Install'} ${entry.title}` : 'Review a skill';
  return (
    <Sheet
      title={title}
      width={860}
      onClose={o.onClose}
      footer={
        installed ? (
          <>
            <Button onClick={o.onClose}>Close</Button>
            <Button variant="primary" onClick={() => navigate({ name: 'skills', skill: skillKey(installed) })}>
              Open skill
            </Button>
          </>
        ) : (
          <>
            <Button onClick={o.onClose}>Cancel</Button>
            <Button
              variant="primary"
              pending={pending}
              disabled={!review || action.kind === 'installed' || action.kind === 'taken' || (action.kind === 'modified' && !replace)}
              onClick={() => void install()}
            >
              {action.kind === 'installed' ? 'Installed' : action.kind === 'update' ? 'Update' : action.kind === 'modified' ? 'Replace and install' : 'Install'}
            </Button>
          </>
        )
      }
    >
      {error ? (
        <p className="field-error" role="alert">
          Couldn't prepare this skill: {error}
        </p>
      ) : !review || !entry ? (
        <p className="muted">Fetching the pinned files and checking them…</p>
      ) : (
        <div className="review">
          <p className="review-summary">{entry.summary}</p>
          <dl className="review-facts">
            <div>
              <dt>Source</dt>
              <dd>
                {review.source_url && entry.source.type === 'github' ? (
                  <>
                    <ExternalLink href={review.source_url}>{sourceLabel(entry)}</ExternalLink> <span className="mono small muted">@ {entry.source.sha.slice(0, 7)}</span>
                  </>
                ) : (
                  'Written by Desk and shipped with the app'
                )}
              </dd>
            </div>
            <div>
              <dt>Licence</dt>
              <dd>
                {entry.license}
                {review.license_text ? (
                  <>
                    {' · '}
                    <button type="button" className="link" aria-expanded={showLicense} onClick={() => setShowLicense((v) => !v)}>
                      {showLicense ? 'Hide text' : 'Read it'}
                    </button>
                  </>
                ) : null}
              </dd>
            </div>
            <div>
              <dt>Desk sets up</dt>
              <dd>
                {runtimeWords(entry)}
                {packages.length ? <span className="mono small muted"> · {packages.join(', ')}</span> : null}
              </dd>
            </div>
            <div>
              <dt>Pinned</dt>
              <dd>
                {review.files.length} files · {bytes(entry.bytes)} · checked against <span className="mono small">{entry.digest.slice(7, 19)}</span>
              </dd>
            </div>
          </dl>
          {showLicense && review.license_text ? <pre className="review-license">{review.license_text}</pre> : null}

          {entry.caveats.length ? (
            <ul className="review-caveats" aria-label="Good to know">
              {entry.caveats.map((c) => (
                <li key={c}>{c}</li>
              ))}
            </ul>
          ) : null}

          {review.warnings.length ? (
            <section className="review-warnings" aria-label="Worth a look">
              <h3>Worth a look · {review.warnings.length}</h3>
              <p className="small muted">Automatic checks found these. They are often harmless; open each one to see it in context.</p>
              <ul>
                {review.warnings.map((w, i) => (
                  <li key={i}>
                    <button type="button" className="link" onClick={() => void openFile(w.file, w.line)}>
                      {w.file}:{w.line}
                    </button>{' '}
                    <span className="small">{WARNING[w.kind]}</span>
                    <code className="review-excerpt">{w.excerpt}</code>
                  </li>
                ))}
              </ul>
            </section>
          ) : (
            <p className="small muted">The automatic checks found nothing unusual.</p>
          )}

          <div className="review-files">
            <ul className="files-list" aria-label="Files">
              {review.files.map((f) => (
                <li key={f.path}>
                  <button type="button" className={`files-entry${file?.path === f.path ? ' current' : ''}`} onClick={() => void openFile(f.path)}>
                    <span className="grow mono">{f.path}</span>
                    {f.script ? <span className="chip chip-wait">script</span> : null}
                    <span className="muted small">{bytes(f.size)}</span>
                  </button>
                </li>
              ))}
            </ul>
            <div className="review-viewer">
              {file ? (
                <>
                  {file.line ? <p className="small muted">Line {file.line}</p> : null}
                  <FileViewer path={file.path} data={file.data} />
                </>
              ) : null}
            </div>
          </div>

          {installed ? (
            <div className="review-done">
              <strong>
                {installed.name} is installed{installed.scope === 'project' ? ` in ${o.projects.find((p) => p.id === installed.projectId)?.name ?? 'the project'}` : ' for every project'}.
              </strong>
              {installedHere ? <RuntimeLine entry={entry} install={installedHere} onRetried={o.onChanged} /> : null}
            </div>
          ) : (
            <>
              <Field id="review-scope" label="Install for">
                <select id="review-scope" className="select" value={scope} onChange={(e) => (setScope(e.target.value), setReplace(false))}>
                  <option value="global">Every project (global)</option>
                  {o.projects.map((p) => (
                    <option key={p.id} value={p.id}>
                      {p.name} only
                    </option>
                  ))}
                </select>
              </Field>
              {action.kind === 'installed' ? <p className="small muted">This version is already installed there.</p> : null}
              {action.kind === 'taken' ? <p className="field-error">A skill named {entry.id} already exists there and didn't come from the catalog. Rename or delete it first.</p> : null}
              {action.kind === 'modified' ? (
                <label className="check">
                  <input type="checkbox" checked={replace} onChange={(e) => setReplace(e.target.checked)} /> {entry.id} was edited after it was installed. Replace those changes (they stay in its history).
                </label>
              ) : null}
            </>
          )}
        </div>
      )}
    </Sheet>
  );
}
