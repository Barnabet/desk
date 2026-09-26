import { ChangeDetectionStrategy, Component, ViewEncapsulation, computed, input, output } from '@angular/core';
import type { ModelInfo, ProjectSettings, ReasoningEffort } from '@desk/protocol';

export type WorkingStyle = Pick<
  ProjectSettings,
  'desk_model' | 'thread_model' | 'fallback_model' | 'desk_reasoning_effort' | 'thread_reasoning_effort' | 'max_concurrent_threads' | 'check_in' | 'autonomy' | 'review_rounds'
>;

export const workingStyleOf = (s: ProjectSettings): WorkingStyle => ({
  desk_model: s.desk_model,
  thread_model: s.thread_model,
  fallback_model: s.fallback_model,
  desk_reasoning_effort: s.desk_reasoning_effort ?? null,
  thread_reasoning_effort: s.thread_reasoning_effort ?? null,
  max_concurrent_threads: s.max_concurrent_threads,
  check_in: s.check_in,
  autonomy: s.autonomy,
  review_rounds: s.review_rounds,
});

export const DEFAULT_STYLE: WorkingStyle = {
  desk_model: 'claude-opus-5-5',
  thread_model: 'claude-opus-5-5',
  fallback_model: null,
  desk_reasoning_effort: null,
  thread_reasoning_effort: null,
  max_concurrent_threads: 4,
  check_in: 'normal',
  autonomy: 'dispatch-freely',
  review_rounds: 2,
};

const CHECK_IN: Array<[WorkingStyle['check_in'], string, string]> = [
  ['minimal', 'Minimal', 'Reports only when done or blocked'],
  ['normal', 'Normal', 'Reports at milestones'],
  ['detailed', 'Detailed', 'Reports every step of the plan'],
];
const AUTONOMY: Array<[WorkingStyle['autonomy'], string, string]> = [
  ['dispatch-freely', 'Dispatch freely', 'Desk starts threads as it sees fit'],
  ['ask-before-dispatch', 'Ask before dispatching', 'Desk proposes threads and waits for your go'],
];
const SLOTS = Array.from({ length: 12 }, (_, i) => i + 1);

/** A number field's value, clamped; anything that is not a number counts as `min` (as SettingsFields.tsx does). */
const clamp = (raw: string, min: number, max: number): number => Math.max(min, Math.min(max, Number(raw) || min));
const val = (e: Event): string => (e.target as HTMLInputElement).value;

/** A model change that also drops a reasoning level the new model does not take. */
function withModel(
  models: ModelInfo[] | null,
  model: string,
  effort: ReasoningEffort | null,
  keys: { model: 'desk_model' | 'thread_model'; effort: 'desk_reasoning_effort' | 'thread_reasoning_effort' },
): Partial<WorkingStyle> {
  const levels = models?.find((m) => m.id === model)?.reasoning_efforts;
  return { [keys.model]: model, ...(effort && levels && !levels.includes(effort) ? { [keys.effort]: null } : {}) };
}

/** One model picker; a value the registry does not know stays listed, marked. */
@Component({
  selector: 'div[deskModelSelect]',
  encapsulation: ViewEncapsulation.None,
  changeDetection: ChangeDetectionStrategy.OnPush,
  host: { class: 'field' },
  template: `
    <label [attr.for]="selectId()">{{ label() }}</label>
    <select [id]="selectId()" class="select" [disabled]="!models()" (change)="pick($event)">
      @if (allowNone()) {
        <option value="" [selected]="!value()">None</option>
      }
      @for (m of options(); track m) {
        <option [value]="m" [selected]="m === value()">{{ m }}{{ models() && !ids().includes(m) ? ' (not in the registry)' : '' }}</option>
      }
    </select>
  `,
})
export class ModelSelect {
  readonly selectId = input.required<string>();
  readonly label = input.required<string>();
  readonly value = input.required<string | null>();
  readonly models = input.required<ModelInfo[] | null>();
  readonly allowNone = input(false);
  readonly picked = output<string | null>();
  protected readonly ids = computed(() => this.models()?.map((m) => m.id) ?? []);
  protected readonly options = computed(() => {
    const v = this.value();
    return v && !this.ids().includes(v) ? [v, ...this.ids()] : this.ids();
  });

  protected pick(e: Event): void {
    this.picked.emit((e.target as HTMLSelectElement).value || null);
  }
}

/** A reasoning level for one role, from the levels its model accepts; empty means the model's default. */
@Component({
  selector: 'div[deskEffortSelect]',
  encapsulation: ViewEncapsulation.None,
  changeDetection: ChangeDetectionStrategy.OnPush,
  host: { class: 'field' },
  template: `
    <label [attr.for]="selectId()">{{ label() }}</label>
    <select [id]="selectId()" class="select" [disabled]="!model() || !levels().length" (change)="pick($event)">
      <option value="" [selected]="!value()">{{ fallback() ? 'Model default (' + fallback() + ')' : 'Model default' }}</option>
      @if (stale()) {
        <option [value]="value() ?? ''" [selected]="true">{{ value() }} (not taken by this model)</option>
      }
      @for (l of levels(); track l) {
        <option [value]="l" [selected]="l === value()">{{ l }}</option>
      }
    </select>
    @if (model(); as m) {
      @if (!levels().length) {
        <p class="field-hint">{{ m.id }} takes no reasoning level (see System → Model registry).</p>
      }
    }
  `,
})
export class EffortSelect {
  readonly selectId = input.required<string>();
  readonly label = input.required<string>();
  readonly value = input.required<ReasoningEffort | null>();
  readonly model = input.required<ModelInfo | undefined>();
  readonly picked = output<ReasoningEffort | null>();
  protected readonly levels = computed(() => this.model()?.reasoning_efforts ?? []);
  protected readonly fallback = computed(() => this.model()?.default_reasoning_effort ?? null);
  protected readonly stale = computed(() => {
    const v = this.value();
    return v !== null && !this.levels().includes(v);
  });

  protected pick(e: Event): void {
    this.picked.emit(((e.target as HTMLSelectElement).value || null) as ReasoningEffort | null);
  }
}

/** How Desk works on a project: check-ins, autonomy, review rounds, models and thread slots (SettingsFields.tsx). */
@Component({
  selector: 'div[deskSettingsFields]',
  encapsulation: ViewEncapsulation.None,
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [EffortSelect, ModelSelect],
  host: { class: 'settings-fields' },
  template: `
    <fieldset class="choice">
      <legend>Check-ins</legend>
      @for (c of checkIn; track c[0]) {
        <label [class.on]="value().check_in === c[0]">
          <input type="radio" [name]="idPrefix() + '-check-in'" [value]="c[0]" [checked]="value().check_in === c[0]" (change)="changed.emit({ check_in: c[0] })" />
          <span><strong>{{ c[1] }}</strong><span class="muted small">{{ c[2] }}</span></span>
        </label>
      }
    </fieldset>
    <fieldset class="choice">
      <legend>Autonomy</legend>
      @for (a of autonomy; track a[0]) {
        <label [class.on]="value().autonomy === a[0]">
          <input type="radio" [name]="idPrefix() + '-autonomy'" [value]="a[0]" [checked]="value().autonomy === a[0]" (change)="changed.emit({ autonomy: a[0] })" />
          <span><strong>{{ a[1] }}</strong><span class="muted small">{{ a[2] }}</span></span>
        </label>
      }
    </fieldset>
    <div class="field">
      <label [attr.for]="idPrefix() + '-rounds'">Review rounds</label>
      <input [id]="idPrefix() + '-rounds'" class="input narrow" type="number" min="0" max="10" [value]="value().review_rounds" (input)="changed.emit({ review_rounds: clamp(val($event), 0, 10) })" />
      <p class="field-hint">How many times Desk may send a thread's work back before accepting or escalating it.</p>
    </div>
    <div class="settings-models">
      <div deskModelSelect [selectId]="idPrefix() + '-desk-model'" label="Desk's model" [value]="value().desk_model" [models]="models()" (picked)="pickModel($event, 'desk')"></div>
      <div deskModelSelect [selectId]="idPrefix() + '-thread-model'" label="Threads' model" [value]="value().thread_model" [models]="models()" (picked)="pickModel($event, 'thread')"></div>
      <div deskModelSelect [selectId]="idPrefix() + '-fallback-model'" label="Fallback when rate limited" [value]="value().fallback_model" [models]="models()" [allowNone]="true" (picked)="changed.emit({ fallback_model: $event })"></div>
      <div deskEffortSelect [selectId]="idPrefix() + '-desk-effort'" label="Desk's reasoning effort" [value]="value().desk_reasoning_effort" [model]="modelOf(value().desk_model)" (picked)="changed.emit({ desk_reasoning_effort: $event })"></div>
      <div deskEffortSelect [selectId]="idPrefix() + '-thread-effort'" label="Threads' reasoning effort" [value]="value().thread_reasoning_effort" [model]="modelOf(value().thread_model)" (picked)="changed.emit({ thread_reasoning_effort: $event })"></div>
    </div>
    <p class="field-hint">Desk can still pick a different level for a single thread (e.g. max for a hard analysis) when it starts it.</p>
    <div class="field">
      <span class="label" [id]="idPrefix() + '-slots-label'">Threads at once · {{ value().max_concurrent_threads }}</span>
      <div class="slots" role="group" [attr.aria-labelledby]="idPrefix() + '-slots-label'">
        @for (n of slots; track n) {
          <button
            type="button"
            [class]="n <= value().max_concurrent_threads ? 'slot on' : 'slot'"
            [attr.aria-label]="n + (n === 1 ? ' thread' : ' threads') + ' at once'"
            [attr.aria-pressed]="n === value().max_concurrent_threads"
            (click)="changed.emit({ max_concurrent_threads: n })"
          ></button>
        }
        <input class="input narrow" type="number" min="1" max="32" aria-label="Threads at once" [value]="value().max_concurrent_threads" (input)="changed.emit({ max_concurrent_threads: clamp(val($event), 1, 32) })" />
      </div>
    </div>
  `,
})
export class SettingsFields {
  readonly value = input.required<WorkingStyle>();
  readonly models = input.required<ModelInfo[] | null>();
  readonly idPrefix = input('settings');
  readonly changed = output<Partial<WorkingStyle>>();
  protected readonly checkIn = CHECK_IN;
  protected readonly autonomy = AUTONOMY;
  protected readonly slots = SLOTS;
  protected readonly clamp = clamp;
  protected readonly val = val;

  protected modelOf(id: string): ModelInfo | undefined {
    return this.models()?.find((m) => m.id === id);
  }

  protected pickModel(id: string | null, role: 'desk' | 'thread'): void {
    if (!id) return;
    const s = this.value();
    this.changed.emit(
      role === 'desk'
        ? withModel(this.models(), id, s.desk_reasoning_effort, { model: 'desk_model', effort: 'desk_reasoning_effort' })
        : withModel(this.models(), id, s.thread_reasoning_effort, { model: 'thread_model', effort: 'thread_reasoning_effort' }),
    );
  }
}
