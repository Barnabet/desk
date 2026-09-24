import { useState, type FormEvent } from 'react';
import { call, DeskCallError } from '../bridge';
import { Button } from '../components/Button';
import { Field } from '../components/Field';
import { toastError } from '../components/Toast';

/** Name, goal, instructions and source folders (native picker). Used by onboarding and the new-project sheet. */
export function ProjectForm({ onCreated, onCancel, submitLabel = 'Create project' }: { onCreated(id: string): void; onCancel?(): void; submitLabel?: string }) {
  const [name, setName] = useState('');
  const [goal, setGoal] = useState('');
  const [instructions, setInstructions] = useState('');
  const [sources, setSources] = useState<string[]>([]);
  const [nameError, setNameError] = useState<string | null>(null);
  const [formError, setFormError] = useState<string | null>(null);
  const [pending, setPending] = useState(false);

  const pick = async () => {
    try {
      const path = await call('app.pickFolder', { purpose: 'source' });
      if (path && !sources.includes(path)) setSources([...sources, path]);
    } catch (err) {
      toastError(err);
    }
  };

  const submit = async (e: FormEvent) => {
    e.preventDefault();
    if (!name.trim()) {
      setNameError('Give the project a name.');
      return;
    }
    setNameError(null);
    setFormError(null);
    setPending(true);
    try {
      const created = await call('projects.create', {
        name: name.trim(),
        goal: goal.trim(),
        ...(instructions.trim() ? { instructions: instructions.trim() } : {}),
        ...(sources.length ? { sources: sources.map((path) => ({ path })) } : {}),
      });
      onCreated(created.project.id);
    } catch (err) {
      if (err instanceof DeskCallError && err.status === 400) setFormError(err.message);
      else toastError(err);
    } finally {
      setPending(false);
    }
  };

  return (
    <form className="sheet-body" onSubmit={submit} noValidate>
      <Field id="project-name" label="Name" error={nameError}>
        <input id="project-name" className="input" value={name} onChange={(e) => setName(e.target.value)} placeholder="Onboarding revamp" />
      </Field>
      <Field id="project-goal" label="Goal" hint="What Desk should work toward. You can refine it later.">
        <textarea id="project-goal" className="textarea" value={goal} onChange={(e) => setGoal(e.target.value)} placeholder="Relaunch onboarding next month" />
      </Field>
      <Field id="project-instructions" label="Standing instructions (optional)">
        <textarea id="project-instructions" className="textarea" value={instructions} onChange={(e) => setInstructions(e.target.value)} />
      </Field>
      <div className="field">
        <span className="eyebrow">Sources</span>
        {sources.length ? (
          <ul className="sources">
            {sources.map((s) => (
              <li key={s}>
                <span>{s}</span>
                <Button size="sm" variant="ghost" aria-label={`Remove ${s}`} onClick={() => setSources(sources.filter((x) => x !== s))}>
                  Remove
                </Button>
              </li>
            ))}
          </ul>
        ) : (
          <p className="field-hint">Folders or git repositories Desk and its threads can read. Optional.</p>
        )}
        <div>
          <Button size="sm" onClick={() => void pick()}>
            Add folder…
          </Button>
        </div>
      </div>
      {formError ? (
        <p className="field-error" role="alert">
          {formError}
        </p>
      ) : null}
      <div className="actions">
        <Button type="submit" variant="primary" pending={pending}>
          {submitLabel}
        </Button>
        {onCancel ? <Button onClick={onCancel}>Cancel</Button> : null}
      </div>
    </form>
  );
}
