import { ChangeDetectionStrategy, Component, computed, inject, input, linkedSignal, output, signal, ViewEncapsulation } from '@angular/core';
import type { InputSpec, InputType, InputValue, ScheduleTrigger } from '@desk/protocol';
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
import { Button } from '../../components/button';
import { Field } from '../../components/field';
import { NowService } from '../../core/now.service';
import { ListEditor } from './list-editor';

const DAY_NAMES = ['Sunday', 'Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday'];
const ZONES = timezones();
const val = (e: Event): string => (e.target as HTMLInputElement).value;
const checked = (e: Event): boolean => (e.target as HTMLInputElement).checked;

/** One schedule: its preset (or custom cron), time, timezone, catch-up, and the inputs it runs with. */
@Component({
  selector: 'fieldset[deskScheduleEditor]',
  imports: [Button, Field],
  changeDetection: ChangeDetectionStrategy.OnPush,
  encapsulation: ViewEncapsulation.None,
  host: { class: 'auto-card', '[attr.aria-label]': "'Schedule ' + (index() + 1)" },
  template: `
    <div class="auto-inline">
      <div deskField [id]="fid('kind')" label="Repeats">
        <select class="select" [id]="fid('kind')" (change)="choose(val($event))">
          <option value="daily" [selected]="preset().kind === 'daily'">Every day</option>
          <option value="weekdays" [selected]="preset().kind === 'weekdays'">Weekdays</option>
          <option value="weekly" [selected]="preset().kind === 'weekly'">Every week</option>
          <option value="monthly" [selected]="preset().kind === 'monthly'">Every month</option>
          <option value="custom" [selected]="preset().kind === 'custom'">Custom (cron)</option>
        </select>
      </div>
      @if (timed(); as p) {
        <div deskField [id]="fid('time')" label="At">
          <input class="input" type="time" [id]="fid('time')" [value]="p.time" (input)="setTime(p, val($event))" />
        </div>
      }
    </div>
    @if (weekly(); as p) {
      <div deskField [id]="fid('day')" label="On">
        <select class="select" [id]="fid('day')" (change)="setPreset({ kind: 'weekly', time: p.time, day: toNumber(val($event)) })">
          @for (d of days; track d; let i = $index) {
            <option [value]="'' + i" [selected]="p.day === i">{{ d }}</option>
          }
        </select>
      </div>
    }
    @if (monthly(); as p) {
      <div deskField [id]="fid('dom')" label="Day of the month" hint="Up to the 28th, which every month has. Use custom cron for later days.">
        <input class="input" type="number" min="1" max="28" [id]="fid('dom')" [value]="p.dom" (input)="setDom(p, val($event))" />
      </div>
    }
    @if (preset().kind === 'custom') {
      <div deskField [id]="fid('cron')" label="Cron" hint="minute hour day-of-month month day-of-week, e.g. */30 9-17 * * 1-5. At most every 5 minutes.">
        <input class="input mono" [id]="fid('cron')" [value]="trigger().cron" (input)="triggerChange.emit({ ...trigger(), cron: val($event) })" />
      </div>
    }
    <div deskField [id]="fid('tz')" label="Timezone">
      <input class="input mono" list="auto-timezones" [id]="fid('tz')" [value]="trigger().timezone" (input)="triggerChange.emit({ ...trigger(), timezone: val($event) })" />
    </div>
    <div deskField [id]="fid('catch')" label="If the computer was asleep">
      <select class="select" [id]="fid('catch')" (change)="setCatchUp(val($event))">
        <option value="once" [selected]="trigger().catch_up === 'once'">Run once when it wakes</option>
        <option value="skip" [selected]="trigger().catch_up === 'skip'">Skip the missed times</option>
      </select>
    </div>
    @if (inputs().length) {
      <fieldset class="field">
        <legend>Inputs for this schedule</legend>
        @for (i of inputs(); track i.key) {
          @if (i.type === 'boolean') {
            <label class="auto-check"><input type="checkbox" [checked]="trigger().inputs?.[i.key] === true" (change)="setInput(i.key, checked($event) || undefined)" /> {{ i.label }}</label>
          } @else {
            <div deskField [id]="fid('in-' + i.key)" [label]="i.label">
              <input class="input" [id]="fid('in-' + i.key)" [type]="i.type === 'number' ? 'number' : 'text'" [value]="stringOf(trigger().inputs?.[i.key])" (input)="setInput(i.key, inputValueOf(i.type, val($event)))" />
            </div>
          }
        }
      </fieldset>
    }
    @if (nextText(); as t) {
      <p class="muted small">{{ t }}</p>
    }
    <div><button deskButton size="sm" variant="ghost" (click)="remove.emit()">Remove schedule</button></div>
  `,
})
export class ScheduleEditor {
  readonly index = input.required<number>();
  readonly trigger = input.required<ScheduleTrigger>();
  readonly inputs = input.required<InputSpec[]>();
  readonly next = input.required<string[]>();
  /** React's onChange. */
  readonly triggerChange = output<ScheduleTrigger>();
  /** React's onRemove. */
  readonly remove = output<void>();

  private readonly now = inject(NowService).now;
  protected readonly val = val;
  protected readonly checked = checked;
  protected readonly inputValueOf = inputValueOf;
  protected readonly days = DAY_NAMES;
  /** Choosing Custom keeps the cron, which may still read as a preset: remember the choice, or the menu would snap back. */
  private readonly custom = signal(false);
  protected readonly preset = computed((): SchedulePreset => {
    const t = this.trigger();
    const derived = presetOf(t.cron);
    return this.custom() || derived.kind === 'custom' ? { kind: 'custom', cron: t.cron } : derived;
  });
  protected readonly timed = computed(() => {
    const p = this.preset();
    return p.kind === 'custom' ? null : p;
  });
  protected readonly weekly = computed(() => {
    const p = this.preset();
    return p.kind === 'weekly' ? p : null;
  });
  protected readonly monthly = computed(() => {
    const p = this.preset();
    return p.kind === 'monthly' ? p : null;
  });
  protected readonly nextText = computed(() => {
    const next = this.next();
    return next.length ? `Next: ${next.slice(0, 3).map((ts) => dayTime(ts, this.now())).join(' · ')}` : null;
  });

  protected fid(f: string): string {
    return `sched-${this.index()}-${f}`;
  }

  protected toNumber(v: string): number {
    return Number(v);
  }

  protected stringOf(v: InputValue | undefined): string {
    return String(v ?? '');
  }

  protected setPreset(p: SchedulePreset): void {
    this.triggerChange.emit({ ...this.trigger(), cron: cronOf(p) });
  }

  protected choose(kind: string): void {
    const k = kind as SchedulePreset['kind'];
    this.custom.set(k === 'custom');
    if (k !== 'custom') this.setPreset(switchPreset(this.preset(), k));
  }

  protected setTime(p: Exclude<SchedulePreset, { kind: 'custom' }>, v: string): void {
    if (v) this.setPreset({ ...p, time: v });
  }

  protected setDom(p: Extract<SchedulePreset, { kind: 'monthly' }>, v: string): void {
    this.setPreset({ ...p, dom: Math.min(28, Math.max(1, Number(v) || 1)) });
  }

  protected setCatchUp(v: string): void {
    this.triggerChange.emit({ ...this.trigger(), catch_up: v as ScheduleTrigger['catch_up'] });
  }

  protected setInput(key: string, v: InputValue | undefined): void {
    const t = this.trigger();
    const rest = Object.fromEntries(Object.entries(t.inputs ?? {}).filter(([k]) => k !== key));
    const next = v === undefined ? rest : { ...rest, [key]: v };
    const { inputs: _old, ...base } = t;
    this.triggerChange.emit(Object.keys(next).length ? { ...base, inputs: next } : base);
  }
}

/** One input: label, key (renames templates on commit), type, required, options, default and description. */
@Component({
  selector: 'fieldset[deskInputEditor]',
  imports: [Button, Field, ListEditor],
  changeDetection: ChangeDetectionStrategy.OnPush,
  encapsulation: ViewEncapsulation.None,
  host: { class: 'auto-card', '[attr.aria-label]': "'Input ' + (index() + 1)" },
  template: `
    <div class="auto-inline">
      <div deskField [id]="fid('label')" label="Label">
        <input class="input" [id]="fid('label')" [value]="spec().label" (input)="set({ label: val($event) })" />
      </div>
      <div deskField [id]="fid('key')" label="Key" [error]="keyError()">
        <input class="input mono" [id]="fid('key')" [value]="key()" (input)="key.set(val($event))" (blur)="commitKey()" (keydown.enter)="commitKey()" />
      </div>
    </div>
    <div class="auto-inline">
      <div deskField [id]="fid('type')" label="Type">
        <select class="select" [id]="fid('type')" (change)="retype(val($event))">
          @for (t of types; track t.value) {
            <option [value]="t.value" [selected]="t.value === spec().type">{{ t.label }}</option>
          }
        </select>
      </div>
      <label class="auto-check"><input type="checkbox" [checked]="spec().required" (change)="set({ required: checked($event) })" /> Required</label>
    </div>
    @if (spec().type === 'choice') {
      <fieldset deskListEditor label="Options" addLabel="Add option" [id]="fid('options')" [values]="spec().options ?? []" [max]="50" (valuesChange)="set({ options: $event.length ? $event : undefined })"></fieldset>
    }
    @switch (defaultKind()) {
      @case ('boolean') {
        <label class="auto-check"><input type="checkbox" [checked]="spec().default === true" (change)="set({ default: checked($event) || undefined })" /> Ticked by default</label>
      }
      @case ('choice') {
        <div deskField [id]="fid('default')" label="Default">
          <select class="select" [id]="fid('default')" (change)="set({ default: val($event) || undefined })">
            <option value="" [selected]="!spec().default">None</option>
            @for (opt of choiceOptions(); track opt) {
              <option [value]="opt" [selected]="opt === spec().default">{{ opt }}</option>
            }
          </select>
        </div>
      }
      @case ('value') {
        <div deskField [id]="fid('default')" label="Default">
          <input class="input" [id]="fid('default')" [type]="spec().type === 'number' ? 'number' : 'text'" [value]="stringOf(spec().default)" (input)="set({ default: inputValueOf(spec().type, val($event)) })" />
        </div>
      }
    }
    <div deskField [id]="fid('description')" label="Description (optional)">
      <input class="input" [id]="fid('description')" [value]="spec().description ?? ''" (input)="set({ description: val($event) || undefined })" />
    </div>
    <div><button deskButton size="sm" variant="ghost" (click)="removeInput()">Remove input</button></div>
  `,
})
export class InputEditor {
  readonly doc = input.required<AutomationDoc>();
  readonly index = input.required<number>();
  readonly spec = input.required<InputSpec>();
  /** React's onChange. */
  readonly docChange = output<AutomationDoc>();

  protected readonly val = val;
  protected readonly checked = checked;
  protected readonly inputValueOf = inputValueOf;
  protected readonly types = (Object.entries(INPUT_TYPE_LABEL) as Array<[InputType, string]>).map(([value, label]) => ({ value, label }));
  private readonly specKey = computed(() => this.spec().key);
  protected readonly key = linkedSignal(() => this.specKey());
  protected readonly keyError = linkedSignal<string, string | null>({ source: this.specKey, computation: () => null });
  protected readonly defaultKind = computed(() => {
    const type = this.spec().type;
    return type === 'boolean' ? 'boolean' : type === 'choice' ? 'choice' : type === 'file' || type === 'folder' ? 'none' : 'value';
  });
  protected readonly choiceOptions = computed(() => (this.spec().options ?? []).filter(Boolean));

  protected fid(f: string): string {
    return `input-${this.index()}-${f}`;
  }

  protected stringOf(v: InputValue | undefined): string {
    return String(v ?? '');
  }

  protected set(p: Partial<Omit<InputSpec, 'key'>>): void {
    this.docChange.emit(patchInput(this.doc(), this.index(), p));
  }

  protected retype(v: string): void {
    this.docChange.emit(retypeInput(this.doc(), this.index(), v as InputType));
  }

  protected commitKey(): void {
    const next = this.key().trim();
    const r = renameInput(this.doc(), this.spec().key, next);
    if ('error' in r) this.keyError.set(r.error);
    else {
      this.keyError.set(null);
      if (next !== this.spec().key) this.docChange.emit(r.doc);
    }
  }

  protected removeInput(): void {
    this.docChange.emit(setInputs(this.doc(), this.doc().def.inputs.filter((_, j) => j !== this.index())));
  }
}

/** The Start pill (spec §8.2): when it runs, and what it asks for. Run now always works. */
@Component({
  selector: 'aside[deskStartInspector]',
  imports: [Button, InputEditor, ScheduleEditor],
  changeDetection: ChangeDetectionStrategy.OnPush,
  encapsulation: ViewEncapsulation.None,
  host: { class: 'auto-inspector', 'aria-label': 'Start' },
  template: `
    <p class="eyebrow">Start</p>
    @if (errors().length) {
      <ul class="auto-issues" role="alert">
        @for (e of errors(); track e) {
          <li>{{ e }}</li>
        }
      </ul>
    }
    <h2>When it runs</h2>
    <p class="muted small">Run now always works. Schedules run it while it is on; Desk cannot wake a sleeping Mac.</p>
    @for (t of doc().def.triggers; track $index; let i = $index) {
      <fieldset deskScheduleEditor [index]="i" [trigger]="t" [inputs]="doc().def.inputs" [next]="nextOf(i)" (triggerChange)="setTrigger(i, $event)" (remove)="removeTrigger(i)"></fieldset>
    }
    @if (doc().def.triggers.length < 10) {
      <div><button deskButton size="sm" (click)="addSchedule()">Add schedule</button></div>
    }
    <h2>Inputs</h2>
    <p class="muted small">{{ inputsNote }}</p>
    @for (spec of doc().def.inputs; track $index; let i = $index) {
      <fieldset deskInputEditor [doc]="doc()" [index]="i" [spec]="spec" (docChange)="docChange.emit($event)"></fieldset>
    }
    @if (doc().def.inputs.length < 20) {
      <div><button deskButton size="sm" (click)="addInput()">Add input</button></div>
    }
    <datalist id="auto-timezones">
      @for (z of zones; track z) {
        <option [value]="z"></option>
      }
    </datalist>
  `,
})
export class StartInspector {
  readonly doc = input.required<AutomationDoc>();
  readonly errors = input.required<string[]>();
  readonly nextTimes = input.required<Record<string, string[]>>();
  /** React's onChange. */
  readonly docChange = output<AutomationDoc>();

  protected readonly zones = ZONES;
  protected readonly inputsNote = 'What Run now and Test ask for. Templates use them as {{inputs.<key>}}.';

  protected nextOf(i: number): string[] {
    return this.nextTimes()[String(i)] ?? [];
  }

  protected setTrigger(i: number, t: ScheduleTrigger): void {
    this.docChange.emit(setTriggers(this.doc(), this.doc().def.triggers.map((x, j) => (j === i ? t : x))));
  }

  protected removeTrigger(i: number): void {
    this.docChange.emit(setTriggers(this.doc(), this.doc().def.triggers.filter((_, j) => j !== i)));
  }

  protected addSchedule(): void {
    this.docChange.emit(setTriggers(this.doc(), [...this.doc().def.triggers, newSchedule()]));
  }

  protected addInput(): void {
    const inputs = this.doc().def.inputs;
    this.docChange.emit(setInputs(this.doc(), [...inputs, newInput(inputs)]));
  }
}
