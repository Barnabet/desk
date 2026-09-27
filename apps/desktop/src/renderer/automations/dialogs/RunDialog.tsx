import { useState } from 'react';
import type { AutomationDefinition, InputSpec } from '@desk/protocol';
import { initialValues, runInputs, type InputDraft } from '@desk/ui-core';
import { call } from '../../bridge';
import { Button } from '../../components/Button';
import { Field } from '../../components/Field';
import { Sheet } from '../../components/Sheet';
import { toastError } from '../../components/Toast';

/** What Run now and Test need to know (an AutomationDetail fits). */
export type RunTarget = { id: string; name: string; title: string; definition: AutomationDefinition };

/** Run now or Test (spec §8.4): a form generated from the automation's inputs. */
export function RunDialog(o: { target: RunTarget; test: boolean; onClose(): void; onStarted(runId: string): void }) {
  const inputs = o.target.definition.inputs;
  const [values, setValues] = useState(() => initialValues(inputs));
  const [pending, setPending] = useState(false);
  const ready = runInputs(inputs, values);
  const start = async () => {
    if (!ready) return;
    setPending(true);
    try {
      const { run_id } = await call('automations.run', { id: o.target.id, req: { inputs: ready, test: o.test } });
      o.onStarted(run_id);
    } catch (err) {
      toastError(err);
      setPending(false);
    }
  };
  return (
    <Sheet
      title={`${o.test ? 'Test' : 'Run'} ${o.target.title}`}
      onClose={o.onClose}
      footer={
        <>
          <Button onClick={o.onClose}>Cancel</Button>
          <Button variant="primary" pending={pending} disabled={!ready} onClick={() => void start()}>
            {o.test ? 'Start test' : 'Run'}
          </Button>
        </>
      }
    >
      {o.test ? (
        <p className="muted small">
          A test is a real run of the current version. Scripts see DESK_TEST=1 and can do a dry run, files publish under automations/{o.target.name}/tests/, and agent and Tell Desk steps act as usual.
        </p>
      ) : null}
      {inputs.length ? (
        inputs.map((i) => <InputField key={i.key} spec={i} value={values[i.key] ?? ''} onChange={(v) => setValues((s) => ({ ...s, [i.key]: v }))} />)
      ) : (
        <p className="muted">This automation takes no inputs.</p>
      )}
    </Sheet>
  );
}

function InputField({ spec, value, onChange }: { spec: InputSpec; value: InputDraft; onChange(v: InputDraft): void }) {
  const id = `run-input-${spec.key}`;
  const label = spec.required ? spec.label : `${spec.label} (optional)`;
  const pick = async (kind: 'file' | 'folder') => {
    try {
      const path = kind === 'file' ? await call('app.pickFile', { purpose: 'automation-input' }) : await call('app.pickFolder', { purpose: 'automation-input' });
      if (path) onChange(path);
    } catch (err) {
      toastError(err);
    }
  };
  switch (spec.type) {
    case 'boolean':
      return (
        <div className="field">
          <label className="auto-check">
            <input id={id} type="checkbox" checked={value === true} onChange={(e) => onChange(e.target.checked)} /> {spec.label}
          </label>
          {spec.description ? <p className="field-hint">{spec.description}</p> : null}
        </div>
      );
    case 'long_text':
      return (
        <Field id={id} label={label} hint={spec.description}>
          <textarea id={id} className="textarea" rows={4} value={String(value)} onChange={(e) => onChange(e.target.value)} />
        </Field>
      );
    case 'choice':
      return (
        <Field id={id} label={label} hint={spec.description}>
          <select id={id} className="select" value={String(value)} onChange={(e) => onChange(e.target.value)}>
            <option value="">{spec.required ? 'Choose…' : 'None'}</option>
            {(spec.options ?? []).map((opt) => (
              <option key={opt} value={opt}>
                {opt}
              </option>
            ))}
          </select>
        </Field>
      );
    case 'number':
      return (
        <Field id={id} label={label} hint={spec.description}>
          <input id={id} className="input" type="number" value={String(value)} onChange={(e) => onChange(e.target.value === '' ? '' : Number(e.target.value))} />
        </Field>
      );
    case 'file':
    case 'folder': {
      const kind = spec.type;
      return (
        <Field id={id} label={label} hint={spec.description ?? (kind === 'file' ? 'Desk copies the file into the run folder.' : 'Desk copies the folder into the run folder.')}>
          <div className="auto-pick">
            <input id={id} className="input mono" value={String(value)} placeholder={kind === 'file' ? '/path/to/file' : '/path/to/folder'} onChange={(e) => onChange(e.target.value)} />
            <Button size="sm" onClick={() => void pick(kind)}>
              Choose…
            </Button>
          </div>
        </Field>
      );
    }
    default:
      return (
        <Field id={id} label={label} hint={spec.description}>
          <input id={id} className="input" type={spec.type === 'url' ? 'url' : 'text'} value={String(value)} onChange={(e) => onChange(e.target.value)} />
        </Field>
      );
  }
}
