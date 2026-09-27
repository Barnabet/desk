import type { AfterRun, AutomationLimits, ValidationIssue } from '@desk/protocol';
import { AFTER_RUN_LABEL, setMeta, type AutomationDoc } from '@desk/ui-core';
import { Field } from '../../components/Field';

const clampInt = (v: string, min: number, max: number) => Math.min(max, Math.max(min, Math.round(Number(v)) || min));

/** With nothing selected: the automation's own settings (spec §2.1, §4.7), its problems and its warnings. */
export function SettingsInspector(o: { doc: AutomationDoc; issues: string[]; warnings: ValidationIssue[]; onChange(doc: AutomationDoc): void }) {
  const def = o.doc.def;
  const setLimit = (k: keyof AutomationLimits, v: number) => o.onChange(setMeta(o.doc, { limits: { ...def.limits, [k]: v } }));
  return (
    <aside className="auto-inspector" aria-label="Automation">
      <p className="eyebrow">Automation</p>
      {o.issues.length ? (
        <ul className="auto-issues" role="alert">
          {o.issues.map((e) => (
            <li key={e}>{e}</li>
          ))}
        </ul>
      ) : null}
      <Field id="auto-title" label="Title">
        <input id="auto-title" className="input" value={def.title} onChange={(e) => o.onChange(setMeta(o.doc, { title: e.target.value }))} />
      </Field>
      <Field id="auto-description" label="Description">
        <textarea id="auto-description" className="textarea" rows={3} value={def.description} onChange={(e) => o.onChange(setMeta(o.doc, { description: e.target.value }))} />
      </Field>
      <Field id="auto-after" label="After each run">
        <select id="auto-after" className="select" value={def.after_run} onChange={(e) => o.onChange(setMeta(o.doc, { after_run: e.target.value as AfterRun }))}>
          {Object.entries(AFTER_RUN_LABEL).map(([v, l]) => (
            <option key={v} value={v}>
              {l}
            </option>
          ))}
        </select>
      </Field>
      <Field id="auto-output" label="Its result, when another automation runs it" hint="That step's outputs and folder; its summary is the run's summary.">
        <select id="auto-output" className="select" value={def.output_step ?? ''} onChange={(e) => o.onChange(setMeta(o.doc, { output_step: e.target.value || undefined }))}>
          <option value="">The last step that succeeded</option>
          {def.steps.map((s) => (
            <option key={s.id} value={s.id}>
              {s.title}
            </option>
          ))}
        </select>
      </Field>
      <div className="auto-inline">
        <Field id="auto-deadline" label="Deadline (hours)">
          <input id="auto-deadline" className="input" type="number" min={1} max={168} value={def.limits.run_deadline_hours} onChange={(e) => setLimit('run_deadline_hours', clampInt(e.target.value, 1, 168))} />
        </Field>
        <Field id="auto-agents" label="Agents at once">
          <input id="auto-agents" className="input" type="number" min={1} max={4} value={def.limits.max_parallel_agents} onChange={(e) => setLimit('max_parallel_agents', clampInt(e.target.value, 1, 4))} />
        </Field>
        <Field id="auto-scripts" label="Scripts at once">
          <input id="auto-scripts" className="input" type="number" min={1} max={8} value={def.limits.max_parallel_scripts} onChange={(e) => setLimit('max_parallel_scripts', clampInt(e.target.value, 1, 8))} />
        </Field>
      </div>
      {o.warnings.length ? (
        <>
          <h2>Warnings</h2>
          <ul className="auto-plain muted">
            {o.warnings.map((w, i) => (
              <li key={i}>{w.message}</li>
            ))}
          </ul>
        </>
      ) : null}
      <p className="muted small">Click Start, a step or an edge to edit it.</p>
    </aside>
  );
}
