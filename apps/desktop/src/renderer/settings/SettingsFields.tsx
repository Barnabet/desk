import type { ModelInfo, ProjectSettings } from '@desk/protocol';

export type WorkingStyle = Pick<ProjectSettings, 'desk_model' | 'thread_model' | 'fallback_model' | 'max_concurrent_threads' | 'check_in' | 'autonomy' | 'review_rounds'>;

export const workingStyleOf = (s: ProjectSettings): WorkingStyle => ({
  desk_model: s.desk_model,
  thread_model: s.thread_model,
  fallback_model: s.fallback_model,
  max_concurrent_threads: s.max_concurrent_threads,
  check_in: s.check_in,
  autonomy: s.autonomy,
  review_rounds: s.review_rounds,
});

export const DEFAULT_STYLE: WorkingStyle = { desk_model: 'claude-opus-5-5', thread_model: 'claude-opus-5-5', fallback_model: null, max_concurrent_threads: 4, check_in: 'normal', autonomy: 'dispatch-freely', review_rounds: 2 };

const CHECK_IN: Array<[WorkingStyle['check_in'], string, string]> = [
  ['minimal', 'Minimal', 'Reports only when done or blocked'],
  ['normal', 'Normal', 'Reports at milestones'],
  ['detailed', 'Detailed', 'Reports every step of the plan'],
];
const AUTONOMY: Array<[WorkingStyle['autonomy'], string, string]> = [
  ['dispatch-freely', 'Dispatch freely', 'Desk starts threads as it sees fit'],
  ['ask-before-dispatch', 'Ask before dispatching', 'Desk proposes threads and waits for your go'],
];
const SLOTS = 12;

function ModelSelect(o: { id: string; label: string; value: string | null; models: ModelInfo[] | null; allowNone?: boolean; onChange(v: string | null): void }) {
  const ids = o.models?.map((m) => m.id) ?? [];
  const options = o.value && !ids.includes(o.value) ? [o.value, ...ids] : ids;
  return (
    <div className="field">
      <label htmlFor={o.id}>{o.label}</label>
      <select id={o.id} className="select" value={o.value ?? ''} onChange={(e) => o.onChange(e.target.value || null)} disabled={!o.models}>
        {o.allowNone ? <option value="">None</option> : null}
        {options.map((m) => (
          <option key={m} value={m}>
            {m}
            {o.models && !ids.includes(m) ? ' (not in the registry)' : ''}
          </option>
        ))}
      </select>
    </div>
  );
}

/** How Desk works on a project: check-ins, autonomy, review rounds, models and thread slots. */
export function SettingsFields({ value, models, onChange, idPrefix = 'settings' }: { value: WorkingStyle; models: ModelInfo[] | null; onChange(patch: Partial<WorkingStyle>): void; idPrefix?: string }) {
  return (
    <div className="settings-fields">
      <fieldset className="choice">
        <legend>Check-ins</legend>
        {CHECK_IN.map(([v, label, hint]) => (
          <label key={v} className={value.check_in === v ? 'on' : undefined}>
            <input type="radio" name={`${idPrefix}-check-in`} value={v} checked={value.check_in === v} onChange={() => onChange({ check_in: v })} />
            <span>
              <strong>{label}</strong>
              <span className="muted small">{hint}</span>
            </span>
          </label>
        ))}
      </fieldset>
      <fieldset className="choice">
        <legend>Autonomy</legend>
        {AUTONOMY.map(([v, label, hint]) => (
          <label key={v} className={value.autonomy === v ? 'on' : undefined}>
            <input type="radio" name={`${idPrefix}-autonomy`} value={v} checked={value.autonomy === v} onChange={() => onChange({ autonomy: v })} />
            <span>
              <strong>{label}</strong>
              <span className="muted small">{hint}</span>
            </span>
          </label>
        ))}
      </fieldset>
      <div className="field">
        <label htmlFor={`${idPrefix}-rounds`}>Review rounds</label>
        <input id={`${idPrefix}-rounds`} className="input narrow" type="number" min={0} max={10} value={value.review_rounds} onChange={(e) => onChange({ review_rounds: Math.max(0, Math.min(10, Number(e.target.value) || 0)) })} />
        <p className="field-hint">How many times Desk may send a thread's work back before accepting or escalating it.</p>
      </div>
      <div className="settings-models">
        <ModelSelect id={`${idPrefix}-desk-model`} label="Desk's model" value={value.desk_model} models={models} onChange={(v) => v && onChange({ desk_model: v })} />
        <ModelSelect id={`${idPrefix}-thread-model`} label="Threads' model" value={value.thread_model} models={models} onChange={(v) => v && onChange({ thread_model: v })} />
        <ModelSelect id={`${idPrefix}-fallback-model`} label="Fallback when rate limited" value={value.fallback_model} models={models} allowNone onChange={(v) => onChange({ fallback_model: v })} />
      </div>
      <div className="field">
        <span className="label" id={`${idPrefix}-slots-label`}>
          Threads at once · {value.max_concurrent_threads}
        </span>
        <div className="slots" role="group" aria-labelledby={`${idPrefix}-slots-label`}>
          {Array.from({ length: SLOTS }, (_, i) => i + 1).map((n) => (
            <button key={n} type="button" className={n <= value.max_concurrent_threads ? 'slot on' : 'slot'} aria-label={`${n} thread${n === 1 ? '' : 's'} at once`} aria-pressed={n === value.max_concurrent_threads} onClick={() => onChange({ max_concurrent_threads: n })} />
          ))}
          <input
            className="input narrow"
            type="number"
            min={1}
            max={32}
            aria-label="Threads at once"
            value={value.max_concurrent_threads}
            onChange={(e) => onChange({ max_concurrent_threads: Math.max(1, Math.min(32, Number(e.target.value) || 1)) })}
          />
        </div>
      </div>
    </div>
  );
}
