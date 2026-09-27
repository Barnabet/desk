import { ChangeDetectionStrategy, Component, computed, input, output, ViewEncapsulation } from '@angular/core';
import type { AfterRun, AutomationLimits, ValidationIssue } from '@desk/protocol';
import { AFTER_RUN_LABEL, setMeta, type AutomationDoc } from '@desk/ui-core';
import { Field } from '../../components/field';

const clampInt = (v: string, min: number, max: number): number => Math.min(max, Math.max(min, Math.round(Number(v)) || min));

/** With nothing selected: the automation's own settings (spec §2.1, §4.7), its problems and its warnings. */
@Component({
  selector: 'aside[deskSettingsInspector]',
  imports: [Field],
  changeDetection: ChangeDetectionStrategy.OnPush,
  encapsulation: ViewEncapsulation.None,
  host: { class: 'auto-inspector', 'aria-label': 'Automation' },
  template: `
    <p class="eyebrow">Automation</p>
    @if (issues().length) {
      <ul class="auto-issues" role="alert">
        @for (e of issues(); track e) {
          <li>{{ e }}</li>
        }
      </ul>
    }
    <div deskField id="auto-title" label="Title">
      <input id="auto-title" class="input" [value]="def().title" (input)="meta({ title: val($event) })" />
    </div>
    <div deskField id="auto-description" label="Description">
      <textarea id="auto-description" class="textarea" rows="3" [value]="def().description" (input)="meta({ description: val($event) })"></textarea>
    </div>
    <div deskField id="auto-after" label="After each run">
      <select id="auto-after" class="select" (change)="setAfter(val($event))">
        @for (a of afterRuns; track a.value) {
          <option [value]="a.value" [selected]="a.value === def().after_run">{{ a.label }}</option>
        }
      </select>
    </div>
    <div deskField id="auto-output" label="Its result, when another automation runs it" hint="That step's outputs and folder; its summary is the run's summary.">
      <select id="auto-output" class="select" (change)="meta({ output_step: val($event) || undefined })">
        <option value="" [selected]="!def().output_step">The last step that succeeded</option>
        @for (s of def().steps; track s.id) {
          <option [value]="s.id" [selected]="s.id === def().output_step">{{ s.title }}</option>
        }
      </select>
    </div>
    <div class="auto-inline">
      <div deskField id="auto-deadline" label="Deadline (hours)">
        <input id="auto-deadline" class="input" type="number" min="1" max="168" [value]="def().limits.run_deadline_hours" (input)="setLimit('run_deadline_hours', clampInt(val($event), 1, 168))" />
      </div>
      <div deskField id="auto-agents" label="Agents at once">
        <input id="auto-agents" class="input" type="number" min="1" max="4" [value]="def().limits.max_parallel_agents" (input)="setLimit('max_parallel_agents', clampInt(val($event), 1, 4))" />
      </div>
      <div deskField id="auto-scripts" label="Scripts at once">
        <input id="auto-scripts" class="input" type="number" min="1" max="8" [value]="def().limits.max_parallel_scripts" (input)="setLimit('max_parallel_scripts', clampInt(val($event), 1, 8))" />
      </div>
    </div>
    @if (warnings().length) {
      <h2>Warnings</h2>
      <ul class="auto-plain muted">
        @for (w of warnings(); track $index) {
          <li>{{ w.message }}</li>
        }
      </ul>
    }
    <p class="muted small">Click Start, a step or an edge to edit it.</p>
  `,
})
export class SettingsInspector {
  readonly doc = input.required<AutomationDoc>();
  readonly issues = input.required<string[]>();
  readonly warnings = input.required<ValidationIssue[]>();
  /** React's onChange. */
  readonly docChange = output<AutomationDoc>();

  protected readonly clampInt = clampInt;
  protected readonly afterRuns = (Object.entries(AFTER_RUN_LABEL) as Array<[AfterRun, string]>).map(([value, label]) => ({ value, label }));
  protected readonly def = computed(() => this.doc().def);

  protected val(e: Event): string {
    return (e.target as HTMLInputElement).value;
  }

  protected meta(patch: Parameters<typeof setMeta>[1]): void {
    this.docChange.emit(setMeta(this.doc(), patch));
  }

  protected setAfter(v: string): void {
    this.meta({ after_run: v as AfterRun });
  }

  protected setLimit(k: keyof AutomationLimits, v: number): void {
    this.meta({ limits: { ...this.def().limits, [k]: v } });
  }
}
