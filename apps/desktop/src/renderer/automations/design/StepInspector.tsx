import { useEffect, useState } from 'react';
import type { OnError, Step } from '@desk/protocol';
import { incomingCount, patchStep, renameStep, routeProblem, STEP_KIND_LABEL, templateSuggestions, type AutomationDoc } from '@desk/ui-core';
import { Button } from '../../components/Button';
import { Field } from '../../components/Field';
import { ListEditor } from './ListEditor';
import { StepKindFields } from './StepKindFields';

const retries = (e: OnError): number | null => (typeof e === 'object' ? e.retry : null);

/** The selected step's fields (spec §8.2): common ones, then its kind's. */
export function StepInspector(o: {
  projectId: string;
  doc: AutomationDoc;
  stepId: string;
  /** This automation's name: Run automation cannot pick it. */
  selfName: string;
  /** Git sources, for an agent's worktree. */
  sources: Array<{ id: string; label: string }>;
  errors: string[];
  onChange(doc: AutomationDoc): void;
  /** The step's id changed: the selection should follow it. */
  onRenamed(id: string): void;
  onDelete(): void;
}) {
  const step = o.doc.def.steps.find((s) => s.id === o.stepId);
  const [idDraft, setIdDraft] = useState(o.stepId);
  const [idError, setIdError] = useState<string | null>(null);
  useEffect(() => {
    setIdDraft(o.stepId);
    setIdError(null);
  }, [o.stepId]);
  if (!step) return null;
  const patch = (p: Partial<Step>) => o.onChange(patchStep(o.doc, step.id, p));
  const commitId = () => {
    const r = renameStep(o.doc, step.id, idDraft.trim());
    if ('error' in r) {
      setIdError(r.error);
      return;
    }
    setIdError(null);
    if (idDraft.trim() !== step.id) {
      o.onChange(r.doc);
      o.onRenamed(idDraft.trim());
    }
  };
  const mode = typeof step.on_error === 'object' ? 'retry' : step.on_error;
  const chooses = step.kind === 'script' || step.kind === 'agent';
  return (
    <aside className="auto-inspector" aria-label="Step">
      <p className="eyebrow">{`${STEP_KIND_LABEL[step.kind]} step`}</p>
      {o.errors.length || idError ? (
        <ul className="auto-issues" role="alert">
          {idError ? <li>{idError}</li> : null}
          {o.errors.map((e) => (
            <li key={e}>{e}</li>
          ))}
        </ul>
      ) : null}
      <Field id="step-title" label="Title">
        <input id="step-title" className="input" value={step.title} onChange={(e) => patch({ title: e.target.value })} />
      </Field>
      <Field id="step-id" label="Id" hint="Templates name this step by it; renaming updates them.">
        <input
          id="step-id"
          className="input mono"
          value={idDraft}
          onChange={(e) => setIdDraft(e.target.value)}
          onBlur={commitId}
          onKeyDown={(e) => {
            if (e.key === 'Enter') commitId();
          }}
        />
      </Field>
      <StepKindFields projectId={o.projectId} selfName={o.selfName} sources={o.sources} step={step} suggest={templateSuggestions(o.doc.def, step.id)} patch={patch} />
      {incomingCount(o.doc.def, step.id) >= 2 ? (
        <Field id="step-join" label="With several steps before it">
          <select id="step-join" className="select" value={step.join} onChange={(e) => patch({ join: e.target.value as Step['join'] })}>
            <option value="all">Wait for all of them, run if one led here</option>
            <option value="any">Run on the first that leads here</option>
          </select>
        </Field>
      ) : null}
      <div className="auto-inline">
        <Field id="step-on-error" label="If it fails">
          <select id="step-on-error" className="select" value={mode} onChange={(e) => patch({ on_error: e.target.value === 'retry' ? { retry: 1 } : (e.target.value as 'stop' | 'continue') })}>
            <option value="stop">Stop the run</option>
            <option value="continue">Continue on route error</option>
            <option value="retry">Try again</option>
          </select>
        </Field>
        {mode === 'retry' ? (
          <Field id="step-retries" label="Attempts after the first">
            <input id="step-retries" className="input" type="number" min={1} max={3} value={retries(step.on_error) ?? 1} onChange={(e) => patch({ on_error: { retry: Math.min(3, Math.max(1, Number(e.target.value) || 1)) } })} />
          </Field>
        ) : null}
      </div>
      {chooses ? (
        <>
          <Field id="step-timeout" label="Time limit in minutes (optional)" hint={step.kind === 'script' ? 'Default 10.' : 'Default 60.'}>
            <input id="step-timeout" className="input" type="number" min={1} max={1440} value={step.timeout_min ?? ''} onChange={(e) => patch({ timeout_min: e.target.value === '' ? undefined : Number(e.target.value) })} />
          </Field>
          <ListEditor id="step-routes" label="Routes" values={step.routes} onChange={(routes) => patch({ routes })} check={routeProblem} addLabel="Add route" max={10} hint="Named outcomes it can choose. A route no edge takes ends that branch." />
          <ListEditor id="step-publish" label="Publish to the Library" values={step.publish} onChange={(publish) => patch({ publish })} placeholder="digest.md" addLabel="Add file" max={20} hint="Globs in its folder, copied to the Library after it succeeds." />
        </>
      ) : null}
      <div>
        <Button variant="danger" size="sm" onClick={o.onDelete}>
          Delete step
        </Button>
      </div>
    </aside>
  );
}
