import { MAX_WAIT_MINUTES, ReasoningEffort, type AgentStep, type AskStep, type ScriptStep, type Step, type SubAutomationStep, type TellDeskStep, type WaitStep } from '@desk/protocol';
import { keyProblem, type TemplateSuggestion } from '@desk/ui-core';
import { Button } from '../../components/Button';
import { Field } from '../../components/Field';
import { ListEditor } from './ListEditor';
import { useAutomationInputs, useAutomationNames, useSkillChoices, useSkillScripts } from './pickers';
import { TemplateField } from './TemplateField';

type Patch = (p: Partial<Step>) => void;
type Ctx = { projectId: string; selfName: string; sources: Array<{ id: string; label: string }>; suggest: TemplateSuggestion[]; patch: Patch };

const optNumber = (v: string): number | undefined => (v === '' ? undefined : Number(v));

/** The fields of one step kind (spec §4). */
export function StepKindFields(o: Ctx & { step: Step }) {
  switch (o.step.kind) {
    case 'script':
      return <ScriptFields {...o} step={o.step} />;
    case 'agent':
      return <AgentFields {...o} step={o.step} />;
    case 'ask':
      return <AskFields {...o} step={o.step} />;
    case 'wait':
      return <WaitFields {...o} step={o.step} />;
    case 'automation':
      return <AutomationFields {...o} step={o.step} />;
    case 'tell_desk':
      return <TellFields {...o} step={o.step} />;
  }
}

function ScriptFields({ projectId, step, suggest, patch }: Ctx & { step: ScriptStep }) {
  const choices = useSkillChoices(projectId);
  const choice = choices.find((c) => c.name === step.skill) ?? null;
  const { scripts } = useSkillScripts(projectId, choice);
  return (
    <>
      <Field id="step-skill" label="Skill" hint="The installed skill whose script runs. Threads draft skills; Desk installs them.">
        <select id="step-skill" className="select" value={step.skill} onChange={(e) => patch({ skill: e.target.value, script: '' })}>
          <option value="">Choose a skill…</option>
          {choices.map((c) => (
            <option key={c.name} value={c.name}>
              {c.source === 'builtin' ? `${c.name} (built-in)` : c.name}
            </option>
          ))}
          {step.skill && !choice ? <option value={step.skill}>{`${step.skill} (not installed)`}</option> : null}
        </select>
      </Field>
      <Field id="step-script" label="Script">
        <select id="step-script" className="select" value={step.script} disabled={!step.skill} onChange={(e) => patch({ script: e.target.value })}>
          <option value="">Choose a script…</option>
          {scripts.map((s) => (
            <option key={s} value={s}>
              {s}
            </option>
          ))}
          {step.script && !scripts.includes(step.script) ? <option value={step.script}>{step.script}</option> : null}
        </select>
      </Field>
      <ListEditor
        id="step-args"
        label="Arguments"
        values={step.args}
        onChange={(args) => patch({ args })}
        suggestions={suggest}
        addLabel="Add argument"
        max={50}
        hint="Each argument goes to the script as it is, never through a shell. {{…}} fills in inputs and earlier steps' results."
      />
      <TemplateField id="step-stdin" label="Standard input (optional)" value={step.stdin ?? ''} onChange={(v) => patch({ stdin: v || undefined })} suggestions={suggest} multiline rows={3} />
      <label className="auto-check">
        <input type="checkbox" checked={step.idempotent} onChange={(e) => patch({ idempotent: e.target.checked })} /> Safe to run again after a crash
      </label>
    </>
  );
}

function AgentFields({ projectId, step, sources, suggest, patch }: Ctx & { step: AgentStep }) {
  const choices = useSkillChoices(projectId);
  const names = [...new Set([...choices.map((c) => c.name), ...step.skills])];
  const toggleSkill = (name: string, on: boolean) => patch({ skills: on ? [...step.skills, name] : step.skills.filter((s) => s !== name) });
  const setKey = (i: number, k: Partial<{ key: string; description: string }>) => patch({ output_keys: step.output_keys.map((x, j) => (j === i ? { ...x, ...k } : x)) });
  return (
    <>
      <TemplateField id="step-brief" label="Brief" value={step.brief} onChange={(v) => patch({ brief: v })} suggestions={suggest} multiline rows={6} hint="What the agent should do. It works in the step's folder and can read earlier steps' folders." />
      <fieldset className="field">
        <legend>Skills</legend>
        {names.length ? (
          names.map((n) => (
            <label key={n} className="auto-check">
              <input type="checkbox" checked={step.skills.includes(n)} disabled={!step.skills.includes(n) && step.skills.length >= 12} onChange={(e) => toggleSkill(n, e.target.checked)} /> {n}
            </label>
          ))
        ) : (
          <p className="muted small">No skills installed yet.</p>
        )}
      </fieldset>
      <fieldset className="field auto-rows">
        <legend>Outputs</legend>
        {step.output_keys.map((k, i) => (
          <div key={i}>
            <div className="auto-row">
              <input aria-label={`Output ${i + 1} key`} className="input mono" value={k.key} placeholder="key" onChange={(e) => setKey(i, { key: e.target.value })} />
              <input aria-label={`Output ${i + 1} meaning`} className="input" value={k.description} placeholder="what it holds" onChange={(e) => setKey(i, { description: e.target.value })} />
              <Button size="sm" variant="ghost" aria-label={`Remove output ${i + 1}`} onClick={() => patch({ output_keys: step.output_keys.filter((_, j) => j !== i) })}>
                ✕
              </Button>
            </div>
            {k.key && keyProblem(k.key) ? <p className="field-error auto-row-error">{keyProblem(k.key)}</p> : null}
          </div>
        ))}
        {step.output_keys.length < 20 ? (
          <div>
            <Button size="sm" onClick={() => patch({ output_keys: [...step.output_keys, { key: '', description: '' }] })}>
              Add output
            </Button>
          </div>
        ) : null}
        <p className="field-hint">What later steps can use as {'{{steps.<id>.outputs.<key>}}'}. The agent sets them when it completes.</p>
      </fieldset>
      <Field id="step-model" label="Model (optional)" hint="Empty uses the project's default.">
        <input id="step-model" className="input mono" value={step.model ?? ''} onChange={(e) => patch({ model: e.target.value || undefined })} />
      </Field>
      <Field id="step-effort" label="Reasoning effort">
        <select id="step-effort" className="select" value={step.reasoning_effort ?? ''} onChange={(e) => patch({ reasoning_effort: (e.target.value || undefined) as AgentStep['reasoning_effort'] })}>
          <option value="">Default</option>
          {ReasoningEffort.options.map((r) => (
            <option key={r} value={r}>
              {r}
            </option>
          ))}
        </select>
      </Field>
      <Field id="step-git" label="Work in a git worktree of" hint="Its branch is desk/auto-<name>-<run>. Desk never merges it.">
        <select id="step-git" className="select" value={step.git_source_id ?? ''} onChange={(e) => patch({ git_source_id: e.target.value || undefined })}>
          <option value="">No worktree (its step folder)</option>
          {sources.map((s) => (
            <option key={s.id} value={s.id}>
              {s.label}
            </option>
          ))}
        </select>
      </Field>
      <Field id="step-max" label="Most tool calls (optional)">
        <input id="step-max" className="input" type="number" min={1} max={400} value={step.max_steps ?? ''} onChange={(e) => patch({ max_steps: optNumber(e.target.value) })} />
      </Field>
    </>
  );
}

function AskFields({ step, suggest, patch }: Ctx & { step: AskStep }) {
  return (
    <>
      <TemplateField id="step-question" label="Question" value={step.question} onChange={(v) => patch({ question: v })} suggestions={suggest} multiline rows={3} />
      <ListEditor id="step-show" label="Files to show" values={step.show} onChange={(show) => patch({ show })} placeholder="digest.md" addLabel="Add file" max={20} hint="Globs of files in earlier steps' folders, shown with the question." />
      <div className="auto-inline">
        <Field id="step-approve" label="Approve button">
          <input id="step-approve" className="input" value={step.approve_label ?? ''} placeholder="Approve" onChange={(e) => patch({ approve_label: e.target.value || undefined })} />
        </Field>
        <Field id="step-reject" label="Reject button">
          <input id="step-reject" className="input" value={step.reject_label ?? ''} placeholder="Reject" onChange={(e) => patch({ reject_label: e.target.value || undefined })} />
        </Field>
      </div>
      <Field id="step-expires" label="Expires after (hours, optional)" hint="Rejecting, or letting it expire, takes the route rejected.">
        <input id="step-expires" className="input" type="number" min={1} max={720} value={step.expires_after_hours ?? ''} onChange={(e) => patch({ expires_after_hours: optNumber(e.target.value) })} />
      </Field>
    </>
  );
}

function WaitFields({ step, patch }: Ctx & { step: WaitStep }) {
  const byTime = step.until !== undefined;
  return (
    <>
      <fieldset className="field">
        <legend>Wait</legend>
        <label className="auto-check">
          <input type="radio" name="wait-mode" checked={!byTime} onChange={() => patch({ minutes: 60, until: undefined })} /> For a number of minutes
        </label>
        <label className="auto-check">
          <input type="radio" name="wait-mode" checked={byTime} onChange={() => patch({ until: '08:00', minutes: undefined })} /> Until a time of day
        </label>
      </fieldset>
      {byTime ? (
        <Field id="step-until" label="Until" hint="In the automation's timezone: its first schedule's, else this computer's.">
          <input id="step-until" className="input" type="time" value={step.until ?? ''} onChange={(e) => patch({ until: e.target.value })} />
        </Field>
      ) : (
        <Field id="step-minutes" label="Minutes" hint="At most a week.">
          <input id="step-minutes" className="input" type="number" min={1} max={MAX_WAIT_MINUTES} value={step.minutes ?? ''} onChange={(e) => patch({ minutes: optNumber(e.target.value) })} />
        </Field>
      )}
    </>
  );
}

function AutomationFields({ projectId, selfName, step, suggest, patch }: Ctx & { step: SubAutomationStep }) {
  const names = useAutomationNames(projectId).filter((n) => n !== selfName);
  const inputs = useAutomationInputs(projectId, step.automation);
  const setInput = (key: string, v: string) => {
    const rest = Object.fromEntries(Object.entries(step.inputs).filter(([k]) => k !== key));
    patch({ inputs: v ? { ...rest, [key]: v } : rest });
  };
  return (
    <>
      <Field id="step-automation" label="Automation to run" hint="Its result is its output step's outputs and folder.">
        <select id="step-automation" className="select" value={step.automation} onChange={(e) => patch({ automation: e.target.value, inputs: {} })}>
          <option value="">Choose an automation…</option>
          {names.map((n) => (
            <option key={n} value={n}>
              {n}
            </option>
          ))}
          {step.automation && !names.includes(step.automation) ? <option value={step.automation}>{step.automation}</option> : null}
        </select>
      </Field>
      {inputs?.map((i) => (
        <TemplateField key={i.key} id={`step-input-${i.key}`} label={i.required ? i.label : `${i.label} (optional)`} value={step.inputs[i.key] ?? ''} onChange={(v) => setInput(i.key, v)} suggestions={suggest} {...(i.description ? { hint: i.description } : {})} />
      ))}
    </>
  );
}

function TellFields({ step, suggest, patch }: Ctx & { step: TellDeskStep }) {
  return (
    <>
      <TemplateField id="step-text" label="Message to Desk" value={step.text} onChange={(v) => patch({ text: v })} suggestions={suggest} multiline rows={4} hint="Desk reads it at its next turn, as a message from this automation." />
      <ListEditor id="step-attach" label="Files to attach" values={step.attach} onChange={(attach) => patch({ attach })} placeholder="report.md" addLabel="Add file" max={20} hint="Globs of files in earlier steps' folders; Desk gets their paths." />
    </>
  );
}
