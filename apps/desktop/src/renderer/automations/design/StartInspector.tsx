import { useEffect, useState } from 'react';
import type { InputSpec, InputType, ScheduleTrigger } from '@desk/protocol';
import {
  cronOf,
  dayTime,
  INPUT_TYPE_LABEL,
  inputValueOf,
  newInput,
  newSchedule,
  patchInput,
  presetOf,
  renameInput,
  retypeInput,
  setInputs,
  setTriggers,
  switchPreset,
  timezones,
  type AutomationDoc,
  type SchedulePreset,
} from '@desk/ui-core';
import { Button } from '../../components/Button';
import { Field } from '../../components/Field';
import { useNow } from '../../state/now';
import { ListEditor } from './ListEditor';

const DAY_NAMES = ['Sunday', 'Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday'];
const ZONES = timezones();

/** The Start pill (spec §8.2): when it runs, and what it asks for. Run now always works. */
export function StartInspector(o: { doc: AutomationDoc; errors: string[]; nextTimes: Record<string, string[]>; onChange(doc: AutomationDoc): void }) {
  const { triggers, inputs } = o.doc.def;
  const setTrigger = (i: number, t: ScheduleTrigger) => o.onChange(setTriggers(o.doc, triggers.map((x, j) => (j === i ? t : x))));
  return (
    <aside className="auto-inspector" aria-label="Start">
      <p className="eyebrow">Start</p>
      {o.errors.length ? (
        <ul className="auto-issues" role="alert">
          {o.errors.map((e) => (
            <li key={e}>{e}</li>
          ))}
        </ul>
      ) : null}
      <h2>When it runs</h2>
      <p className="muted small">Run now always works. Schedules run it while it is on; Desk cannot wake a sleeping Mac.</p>
      {triggers.map((t, i) => (
        <ScheduleEditor key={i} index={i} trigger={t} inputs={inputs} next={o.nextTimes[String(i)] ?? []} onChange={(n) => setTrigger(i, n)} onRemove={() => o.onChange(setTriggers(o.doc, triggers.filter((_, j) => j !== i)))} />
      ))}
      {triggers.length < 10 ? (
        <div>
          <Button size="sm" onClick={() => o.onChange(setTriggers(o.doc, [...triggers, newSchedule()]))}>
            Add schedule
          </Button>
        </div>
      ) : null}
      <h2>Inputs</h2>
      <p className="muted small">What Run now and Test ask for. Templates use them as {'{{inputs.<key>}}'}.</p>
      {inputs.map((spec, i) => (
        <InputEditor key={i} doc={o.doc} index={i} spec={spec} onChange={o.onChange} />
      ))}
      {inputs.length < 20 ? (
        <div>
          <Button size="sm" onClick={() => o.onChange(setInputs(o.doc, [...inputs, newInput(inputs)]))}>
            Add input
          </Button>
        </div>
      ) : null}
      <datalist id="auto-timezones">
        {ZONES.map((z) => (
          <option key={z} value={z} />
        ))}
      </datalist>
    </aside>
  );
}

function ScheduleEditor(o: { index: number; trigger: ScheduleTrigger; inputs: InputSpec[]; next: string[]; onChange(t: ScheduleTrigger): void; onRemove(): void }) {
  const now = useNow();
  const t = o.trigger;
  // Choosing Custom keeps the cron, which may still read as a preset: remember the choice, or the menu would snap back.
  const [custom, setCustom] = useState(false);
  const derived = presetOf(t.cron);
  const preset: SchedulePreset = custom || derived.kind === 'custom' ? { kind: 'custom', cron: t.cron } : derived;
  const id = (f: string) => `sched-${o.index}-${f}`;
  const setPreset = (p: SchedulePreset) => o.onChange({ ...t, cron: cronOf(p) });
  const choose = (kind: SchedulePreset['kind']) => {
    setCustom(kind === 'custom');
    if (kind !== 'custom') setPreset(switchPreset(preset, kind));
  };
  const setInput = (key: string, v: ReturnType<typeof inputValueOf>) => {
    const rest = Object.fromEntries(Object.entries(t.inputs ?? {}).filter(([k]) => k !== key));
    const next = v === undefined ? rest : { ...rest, [key]: v };
    const { inputs: _old, ...base } = t;
    o.onChange(Object.keys(next).length ? { ...base, inputs: next } : base);
  };
  return (
    <fieldset className="auto-card" aria-label={`Schedule ${o.index + 1}`}>
      <div className="auto-inline">
        <Field id={id('kind')} label="Repeats">
          <select id={id('kind')} className="select" value={preset.kind} onChange={(e) => choose(e.target.value as SchedulePreset['kind'])}>
            <option value="daily">Every day</option>
            <option value="weekdays">Weekdays</option>
            <option value="weekly">Every week</option>
            <option value="monthly">Every month</option>
            <option value="custom">Custom (cron)</option>
          </select>
        </Field>
        {preset.kind !== 'custom' ? (
          <Field id={id('time')} label="At">
            <input id={id('time')} className="input" type="time" value={preset.time} onChange={(e) => e.target.value && setPreset({ ...preset, time: e.target.value })} />
          </Field>
        ) : null}
      </div>
      {preset.kind === 'weekly' ? (
        <Field id={id('day')} label="On">
          <select id={id('day')} className="select" value={String(preset.day)} onChange={(e) => setPreset({ ...preset, day: Number(e.target.value) })}>
            {DAY_NAMES.map((d, i) => (
              <option key={d} value={String(i)}>
                {d}
              </option>
            ))}
          </select>
        </Field>
      ) : null}
      {preset.kind === 'monthly' ? (
        <Field id={id('dom')} label="Day of the month" hint="Up to the 28th, which every month has. Use custom cron for later days.">
          <input id={id('dom')} className="input" type="number" min={1} max={28} value={preset.dom} onChange={(e) => setPreset({ ...preset, dom: Math.min(28, Math.max(1, Number(e.target.value) || 1)) })} />
        </Field>
      ) : null}
      {preset.kind === 'custom' ? (
        <Field id={id('cron')} label="Cron" hint="minute hour day-of-month month day-of-week, e.g. */30 9-17 * * 1-5. At most every 5 minutes.">
          <input id={id('cron')} className="input mono" value={t.cron} onChange={(e) => o.onChange({ ...t, cron: e.target.value })} />
        </Field>
      ) : null}
      <Field id={id('tz')} label="Timezone">
        <input id={id('tz')} className="input mono" list="auto-timezones" value={t.timezone} onChange={(e) => o.onChange({ ...t, timezone: e.target.value })} />
      </Field>
      <Field id={id('catch')} label="If the computer was asleep">
        <select id={id('catch')} className="select" value={t.catch_up} onChange={(e) => o.onChange({ ...t, catch_up: e.target.value as ScheduleTrigger['catch_up'] })}>
          <option value="once">Run once when it wakes</option>
          <option value="skip">Skip the missed times</option>
        </select>
      </Field>
      {o.inputs.length ? (
        <fieldset className="field">
          <legend>Inputs for this schedule</legend>
          {o.inputs.map((i) =>
            i.type === 'boolean' ? (
              <label key={i.key} className="auto-check">
                <input type="checkbox" checked={t.inputs?.[i.key] === true} onChange={(e) => setInput(i.key, e.target.checked || undefined)} /> {i.label}
              </label>
            ) : (
              <Field key={i.key} id={id(`in-${i.key}`)} label={i.label}>
                <input id={id(`in-${i.key}`)} className="input" type={i.type === 'number' ? 'number' : 'text'} value={String(t.inputs?.[i.key] ?? '')} onChange={(e) => setInput(i.key, inputValueOf(i.type, e.target.value))} />
              </Field>
            ),
          )}
        </fieldset>
      ) : null}
      {o.next.length ? <p className="muted small">{`Next: ${o.next.slice(0, 3).map((ts) => dayTime(ts, now)).join(' · ')}`}</p> : null}
      <div>
        <Button size="sm" variant="ghost" onClick={o.onRemove}>
          Remove schedule
        </Button>
      </div>
    </fieldset>
  );
}

function InputEditor(o: { doc: AutomationDoc; index: number; spec: InputSpec; onChange(doc: AutomationDoc): void }) {
  const { spec, index } = o;
  const [key, setKey] = useState(spec.key);
  const [keyError, setKeyError] = useState<string | null>(null);
  useEffect(() => {
    setKey(spec.key);
    setKeyError(null);
  }, [spec.key]);
  const id = (f: string) => `input-${index}-${f}`;
  const set = (p: Partial<Omit<InputSpec, 'key'>>) => o.onChange(patchInput(o.doc, index, p));
  const commitKey = () => {
    const r = renameInput(o.doc, spec.key, key.trim());
    if ('error' in r) setKeyError(r.error);
    else {
      setKeyError(null);
      if (key.trim() !== spec.key) o.onChange(r.doc);
    }
  };
  return (
    <fieldset className="auto-card" aria-label={`Input ${index + 1}`}>
      <div className="auto-inline">
        <Field id={id('label')} label="Label">
          <input id={id('label')} className="input" value={spec.label} onChange={(e) => set({ label: e.target.value })} />
        </Field>
        <Field id={id('key')} label="Key" error={keyError}>
          <input id={id('key')} className="input mono" value={key} onChange={(e) => setKey(e.target.value)} onBlur={commitKey} onKeyDown={(e) => e.key === 'Enter' && commitKey()} />
        </Field>
      </div>
      <div className="auto-inline">
        <Field id={id('type')} label="Type">
          <select id={id('type')} className="select" value={spec.type} onChange={(e) => o.onChange(retypeInput(o.doc, index, e.target.value as InputType))}>
            {Object.entries(INPUT_TYPE_LABEL).map(([v, l]) => (
              <option key={v} value={v}>
                {l}
              </option>
            ))}
          </select>
        </Field>
        <label className="auto-check">
          <input type="checkbox" checked={spec.required} onChange={(e) => set({ required: e.target.checked })} /> Required
        </label>
      </div>
      {spec.type === 'choice' ? <ListEditor id={id('options')} label="Options" values={spec.options ?? []} onChange={(options) => set({ options: options.length ? options : undefined })} addLabel="Add option" max={50} /> : null}
      {spec.type === 'boolean' ? (
        <label className="auto-check">
          <input type="checkbox" checked={spec.default === true} onChange={(e) => set({ default: e.target.checked || undefined })} /> Ticked by default
        </label>
      ) : spec.type === 'choice' ? (
        <Field id={id('default')} label="Default">
          <select id={id('default')} className="select" value={String(spec.default ?? '')} onChange={(e) => set({ default: e.target.value || undefined })}>
            <option value="">None</option>
            {(spec.options ?? []).filter(Boolean).map((opt) => (
              <option key={opt} value={opt}>
                {opt}
              </option>
            ))}
          </select>
        </Field>
      ) : spec.type === 'file' || spec.type === 'folder' ? null : (
        <Field id={id('default')} label="Default">
          <input id={id('default')} className="input" type={spec.type === 'number' ? 'number' : 'text'} value={String(spec.default ?? '')} onChange={(e) => set({ default: inputValueOf(spec.type, e.target.value) })} />
        </Field>
      )}
      <Field id={id('description')} label="Description (optional)">
        <input id={id('description')} className="input" value={spec.description ?? ''} onChange={(e) => set({ description: e.target.value || undefined })} />
      </Field>
      <div>
        <Button size="sm" variant="ghost" onClick={() => o.onChange(setInputs(o.doc, o.doc.def.inputs.filter((_, j) => j !== index)))}>
          Remove input
        </Button>
      </div>
    </fieldset>
  );
}
