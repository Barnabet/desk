import { useRef, useState } from 'react';
import type { SkillDetail } from '@desk/client';
import { SkillName } from '@desk/protocol';
import { call, DeskCallError } from '../bridge';
import { Button } from '../components/Button';
import { Field } from '../components/Field';
import { Sheet } from '../components/Sheet';
import { toastError } from '../components/Toast';
import { fileToBase64, MAX_UPLOAD, textToBase64 } from '../files';
import { bytes } from '../format';
import { scopeArg, type SkillRef } from './data';

type NewFile = { path: string; content: string | File };

/** Create a skill, or refine one by hand: description, instructions (SKILL.md), files to add or remove, and a change note. */
export function SkillEditor(o: { skill?: { ref: SkillRef; detail: SkillDetail }; projects: Array<{ id: string; name: string }>; defaultProjectId?: string; onSaved(ref: SkillRef): void; onClose(): void }) {
  const editing = o.skill;
  const [name, setName] = useState(editing?.ref.name ?? '');
  const [scope, setScope] = useState<string>(editing ? (editing.ref.scope === 'global' ? 'global' : editing.ref.projectId!) : (o.defaultProjectId ?? 'global'));
  const [description, setDescription] = useState(editing?.detail.description ?? '');
  const [instructions, setInstructions] = useState(editing?.detail.instructions ?? '');
  const [remove, setRemove] = useState<Set<string>>(new Set());
  const [added, setAdded] = useState<NewFile[]>([]);
  const [folder, setFolder] = useState('scripts/');
  const [textPath, setTextPath] = useState('');
  const [textBody, setTextBody] = useState('');
  const [note, setNote] = useState('');
  const [errors, setErrors] = useState<Record<string, string>>({});
  const [pending, setPending] = useState(false);
  const pickRef = useRef<HTMLInputElement>(null);
  const existing = (editing?.detail.files ?? []).filter((f) => f.path !== 'SKILL.md');

  const addUploads = (files: FileList | null) => {
    const prefix = folder.trim().replace(/^\/+/, '');
    const next: NewFile[] = [];
    for (const f of Array.from(files ?? [])) {
      if (f.size > MAX_UPLOAD) {
        toastError(new Error(`${f.name} is larger than 25 MB.`));
        continue;
      }
      next.push({ path: `${prefix && !prefix.endsWith('/') ? `${prefix}/` : prefix}${f.name}`, content: f });
    }
    setAdded((a) => [...a.filter((x) => !next.some((n) => n.path === x.path)), ...next]);
    if (pickRef.current) pickRef.current.value = '';
  };

  const save = async () => {
    const e: Record<string, string> = {};
    const parsedName = SkillName.safeParse(name.trim());
    if (!editing && !parsedName.success) e.name = parsedName.error.issues[0]?.message ?? 'Invalid name';
    if (!description.trim()) e.description = 'Say in one line what the skill is for; Desk uses it to pick skills.';
    if (!instructions.trim()) e.instructions = 'Write the instructions agents follow.';
    setErrors(e);
    if (Object.keys(e).length) return;
    const ref: SkillRef = editing ? editing.ref : scope === 'global' ? { scope: 'global', name: name.trim() } : { scope: 'project', projectId: scope, name: name.trim() };
    setPending(true);
    try {
      const files = await Promise.all(added.map(async (f) => ({ path: f.path, content_base64: typeof f.content === 'string' ? await textToBase64(f.content) : await fileToBase64(f.content) })));
      await call('skills.save', {
        ...scopeArg(ref),
        name: ref.name,
        skill: {
          ...(!editing || description.trim() !== editing.detail.description ? { description: description.trim() } : {}),
          ...(!editing || instructions !== editing.detail.instructions ? { instructions } : {}),
          files,
          remove_files: [...remove],
          ...(note.trim() ? { change_note: note.trim() } : {}),
        },
      });
      o.onSaved(ref);
    } catch (err) {
      if (err instanceof DeskCallError && err.status === 400) setErrors({ form: err.message });
      else toastError(err);
    } finally {
      setPending(false);
    }
  };

  return (
    <Sheet
      title={editing ? `Edit ${editing.ref.name}` : 'New skill'}
      width={720}
      onClose={o.onClose}
      footer={
        <>
          <Button onClick={o.onClose}>Cancel</Button>
          <Button variant="primary" pending={pending} onClick={() => void save()}>
            {editing ? 'Save new version' : 'Create skill'}
          </Button>
        </>
      }
    >
      {editing ? null : (
        <div className="skill-editor-row">
          <Field id="skill-name" label="Name" error={errors.name ?? null} hint="Lowercase words joined by hyphens, like weekly-report.">
            <input id="skill-name" className="input mono" value={name} onChange={(e) => setName(e.target.value)} />
          </Field>
          <Field id="skill-scope" label="Scope">
            <select id="skill-scope" className="select" value={scope} onChange={(e) => setScope(e.target.value)}>
              <option value="global">Global (every project)</option>
              {o.projects.map((p) => (
                <option key={p.id} value={p.id}>
                  {p.name} only
                </option>
              ))}
            </select>
          </Field>
        </div>
      )}
      <Field id="skill-description" label="Description" error={errors.description ?? null}>
        <input id="skill-description" className="input" maxLength={1024} value={description} onChange={(e) => setDescription(e.target.value)} />
      </Field>
      <Field id="skill-instructions" label="Instructions (SKILL.md)" error={errors.instructions ?? null} hint="Markdown. Scripts in the skill run with skill_run, in the sandbox.">
        <textarea id="skill-instructions" className="textarea mono" rows={12} value={instructions} onChange={(e) => setInstructions(e.target.value)} />
      </Field>
      <div className="field">
        <span className="label">Files</span>
        {existing.length || added.length ? (
          <ul className="skill-files-edit">
            {existing.map((f) => (
              <li key={f.path} className={remove.has(f.path) ? 'removing' : undefined}>
                <span className="mono grow">{f.path}</span>
                <span className="muted small">{bytes(f.size)}</span>
                <label className="small">
                  <input
                    type="checkbox"
                    checked={remove.has(f.path)}
                    onChange={(e) =>
                      setRemove((r) => {
                        const n = new Set(r);
                        if (e.target.checked) n.add(f.path);
                        else n.delete(f.path);
                        return n;
                      })
                    }
                  />{' '}
                  Remove
                </label>
              </li>
            ))}
            {added.map((f) => (
              <li key={f.path} className="adding">
                <span className="mono grow">{f.path}</span>
                <span className="muted small">{typeof f.content === 'string' ? 'new text file' : bytes(f.content.size)}</span>
                <Button size="sm" variant="ghost" aria-label={`Don't add ${f.path}`} onClick={() => setAdded((a) => a.filter((x) => x.path !== f.path))}>
                  ✕
                </Button>
              </li>
            ))}
          </ul>
        ) : (
          <p className="field-hint">No files besides SKILL.md.</p>
        )}
        <div className="skill-add-files">
          <input className="input mono" aria-label="Folder for uploads" value={folder} onChange={(e) => setFolder(e.target.value)} />
          <Button size="sm" onClick={() => pickRef.current?.click()}>
            Upload files…
          </Button>
          <input ref={pickRef} type="file" multiple hidden data-testid="skill-upload" onChange={(e) => addUploads(e.target.files)} />
        </div>
        <details className="skill-new-text">
          <summary>New text file</summary>
          <div className="skill-add-files">
            <input className="input mono" aria-label="New file path" placeholder="scripts/check.py" value={textPath} onChange={(e) => setTextPath(e.target.value)} />
            <Button
              size="sm"
              disabled={!textPath.trim()}
              onClick={() => {
                const path = textPath.trim().replace(/^\/+/, '');
                setAdded((a) => [...a.filter((x) => x.path !== path), { path, content: textBody }]);
                setTextPath('');
                setTextBody('');
              }}
            >
              Add file
            </Button>
          </div>
          <textarea className="textarea mono" aria-label="New file content" rows={6} value={textBody} onChange={(e) => setTextBody(e.target.value)} />
        </details>
      </div>
      <Field id="skill-note" label="Change note (optional)" hint="Shown in the history next to this version.">
        <input id="skill-note" className="input" value={note} onChange={(e) => setNote(e.target.value)} />
      </Field>
      {errors.form ? (
        <p className="field-error" role="alert">
          {errors.form}
        </p>
      ) : null}
    </Sheet>
  );
}
