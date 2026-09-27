import { ChangeDetectionStrategy, Component, computed, input, output, ViewEncapsulation } from '@angular/core';
import { MAX_WAIT_MINUTES, ReasoningEffort, type AgentStep, type AskStep, type ScriptStep, type Step, type SubAutomationStep, type TellDeskStep, type WaitStep } from '@desk/protocol';
import { keyProblem, type TemplateSuggestion } from '@desk/ui-core';
import { Button } from '../../components/button';
import { Field } from '../../components/field';
import { ListEditor } from './list-editor';
import { injectAutomationInputs, injectAutomationNames, injectSkillChoices, injectSkillScripts } from './pickers';
import { TemplateField } from './template-field';

type Sources = Array<{ id: string; label: string }>;
const optNumber = (v: string): number | undefined => (v === '' ? undefined : Number(v));
const val = (e: Event): string => (e.target as HTMLInputElement).value;
const checked = (e: Event): boolean => (e.target as HTMLInputElement).checked;

@Component({
  selector: 'div[deskScriptFields]',
  imports: [Field, ListEditor, TemplateField],
  changeDetection: ChangeDetectionStrategy.OnPush,
  encapsulation: ViewEncapsulation.None,
  host: { style: 'display: contents' },
  template: `
    <div deskField id="step-skill" label="Skill" hint="The installed skill whose script runs. Threads draft skills; Desk installs them.">
      <select id="step-skill" class="select" (change)="patch.emit({ skill: val($event), script: '' })">
        <option value="" [selected]="!step().skill">Choose a skill…</option>
        @for (c of choices(); track c.name) {
          <option [value]="c.name" [selected]="c.name === step().skill">{{ c.source === 'builtin' ? c.name + ' (built-in)' : c.name }}</option>
        }
        @if (step().skill && !choice()) {
          <option [value]="step().skill" [selected]="true">{{ step().skill + ' (not installed)' }}</option>
        }
      </select>
    </div>
    <div deskField id="step-script" label="Script">
      <select id="step-script" class="select" [disabled]="!step().skill" (change)="patch.emit({ script: val($event) })">
        <option value="" [selected]="!step().script">Choose a script…</option>
        @for (s of scripts().scripts; track s) {
          <option [value]="s" [selected]="s === step().script">{{ s }}</option>
        }
        @if (step().script && !scripts().scripts.includes(step().script)) {
          <option [value]="step().script" [selected]="true">{{ step().script }}</option>
        }
      </select>
    </div>
    <fieldset deskListEditor id="step-args" label="Arguments" [values]="step().args" [suggestions]="suggest()" addLabel="Add argument" [max]="50" [hint]="argsHint" (valuesChange)="patch.emit({ args: $event })"></fieldset>
    <div deskTemplateField id="step-stdin" label="Standard input (optional)" [value]="step().stdin ?? ''" [suggestions]="suggest()" [multiline]="true" [rows]="3" (valueChange)="patch.emit({ stdin: $event || undefined })"></div>
    <label class="auto-check"><input type="checkbox" [checked]="step().idempotent" (change)="patch.emit({ idempotent: checked($event) })" /> Safe to run again after a crash</label>
  `,
})
export class ScriptFields {
  readonly projectId = input.required<string>();
  readonly step = input.required<ScriptStep>();
  readonly suggest = input.required<TemplateSuggestion[]>();
  readonly patch = output<Partial<Step>>();
  protected readonly val = val;
  protected readonly checked = checked;
  protected readonly argsHint = "Each argument goes to the script as it is, never through a shell. {{…}} fills in inputs and earlier steps' results.";
  protected readonly choices = injectSkillChoices(() => this.projectId());
  protected readonly choice = computed(() => this.choices().find((c) => c.name === this.step().skill) ?? null);
  protected readonly scripts = injectSkillScripts(() => this.projectId(), () => this.choice());
}

@Component({
  selector: 'div[deskAgentFields]',
  imports: [Button, Field, TemplateField],
  changeDetection: ChangeDetectionStrategy.OnPush,
  encapsulation: ViewEncapsulation.None,
  host: { style: 'display: contents' },
  template: `
    <div deskTemplateField id="step-brief" label="Brief" [value]="step().brief" [suggestions]="suggest()" [multiline]="true" [rows]="6" hint="What the agent should do. It works in the step's folder and can read earlier steps' folders." (valueChange)="patch.emit({ brief: $event })"></div>
    <fieldset class="field">
      <legend>Skills</legend>
      @for (n of names(); track n) {
        <label class="auto-check"><input type="checkbox" [checked]="step().skills.includes(n)" [disabled]="!step().skills.includes(n) && step().skills.length >= 12" (change)="toggleSkill(n, checked($event))" /> {{ n }}</label>
      } @empty {
        <p class="muted small">No skills installed yet.</p>
      }
    </fieldset>
    <fieldset class="field auto-rows">
      <legend>Outputs</legend>
      @for (k of step().output_keys; track $index; let i = $index) {
        <div>
          <div class="auto-row">
            <input class="input mono" placeholder="key" [attr.aria-label]="'Output ' + (i + 1) + ' key'" [value]="k.key" (input)="setKey(i, { key: val($event) })" />
            <input class="input" placeholder="what it holds" [attr.aria-label]="'Output ' + (i + 1) + ' meaning'" [value]="k.description" (input)="setKey(i, { description: val($event) })" />
            <button deskButton size="sm" variant="ghost" [attr.aria-label]="'Remove output ' + (i + 1)" (click)="removeKey(i)">✕</button>
          </div>
          @if (k.key && keyProblem(k.key); as problem) {
            <p class="field-error auto-row-error">{{ problem }}</p>
          }
        </div>
      }
      @if (step().output_keys.length < 20) {
        <div><button deskButton size="sm" (click)="patch.emit({ output_keys: [...step().output_keys, { key: '', description: '' }] })">Add output</button></div>
      }
      <p class="field-hint">{{ outputsHint }}</p>
    </fieldset>
    <div deskField id="step-model" label="Model (optional)" hint="Empty uses the project's default.">
      <input id="step-model" class="input mono" [value]="step().model ?? ''" (input)="patch.emit({ model: val($event) || undefined })" />
    </div>
    <div deskField id="step-effort" label="Reasoning effort">
      <select id="step-effort" class="select" (change)="setEffort(val($event))">
        <option value="" [selected]="!step().reasoning_effort">Default</option>
        @for (r of efforts; track r) {
          <option [value]="r" [selected]="r === step().reasoning_effort">{{ r }}</option>
        }
      </select>
    </div>
    <div deskField id="step-git" label="Work in a git worktree of" [hint]="gitHint">
      <select id="step-git" class="select" (change)="patch.emit({ git_source_id: val($event) || undefined })">
        <option value="" [selected]="!step().git_source_id">No worktree (its step folder)</option>
        @for (s of sources(); track s.id) {
          <option [value]="s.id" [selected]="s.id === step().git_source_id">{{ s.label }}</option>
        }
      </select>
    </div>
    <div deskField id="step-max" label="Most tool calls (optional)">
      <input id="step-max" class="input" type="number" min="1" max="400" [value]="step().max_steps ?? ''" (input)="patch.emit({ max_steps: optNumber(val($event)) })" />
    </div>
  `,
})
export class AgentFields {
  readonly projectId = input.required<string>();
  readonly sources = input.required<Sources>();
  readonly step = input.required<AgentStep>();
  readonly suggest = input.required<TemplateSuggestion[]>();
  readonly patch = output<Partial<Step>>();
  protected readonly val = val;
  protected readonly checked = checked;
  protected readonly optNumber = optNumber;
  protected readonly keyProblem = keyProblem;
  protected readonly efforts = ReasoningEffort.options;
  protected readonly outputsHint = 'What later steps can use as {{steps.<id>.outputs.<key>}}. The agent sets them when it completes.';
  protected readonly gitHint = 'Its branch is desk/auto-<name>-<run>. Desk never merges it.';
  private readonly choices = injectSkillChoices(() => this.projectId());
  protected readonly names = computed(() => [...new Set([...this.choices().map((c) => c.name), ...this.step().skills])]);

  protected toggleSkill(name: string, on: boolean): void {
    const skills = this.step().skills;
    this.patch.emit({ skills: on ? [...skills, name] : skills.filter((s) => s !== name) });
  }

  protected setKey(i: number, k: Partial<{ key: string; description: string }>): void {
    this.patch.emit({ output_keys: this.step().output_keys.map((x, j) => (j === i ? { ...x, ...k } : x)) });
  }

  protected removeKey(i: number): void {
    this.patch.emit({ output_keys: this.step().output_keys.filter((_, j) => j !== i) });
  }

  protected setEffort(v: string): void {
    this.patch.emit({ reasoning_effort: (v || undefined) as AgentStep['reasoning_effort'] });
  }
}

@Component({
  selector: 'div[deskAskFields]',
  imports: [Field, ListEditor, TemplateField],
  changeDetection: ChangeDetectionStrategy.OnPush,
  encapsulation: ViewEncapsulation.None,
  host: { style: 'display: contents' },
  template: `
    <div deskTemplateField id="step-question" label="Question" [value]="step().question" [suggestions]="suggest()" [multiline]="true" [rows]="3" (valueChange)="patch.emit({ question: $event })"></div>
    <fieldset deskListEditor id="step-show" label="Files to show" [values]="step().show" placeholder="digest.md" addLabel="Add file" [max]="20" hint="Globs of files in earlier steps' folders, shown with the question." (valuesChange)="patch.emit({ show: $event })"></fieldset>
    <div class="auto-inline">
      <div deskField id="step-approve" label="Approve button">
        <input id="step-approve" class="input" placeholder="Approve" [value]="step().approve_label ?? ''" (input)="patch.emit({ approve_label: val($event) || undefined })" />
      </div>
      <div deskField id="step-reject" label="Reject button">
        <input id="step-reject" class="input" placeholder="Reject" [value]="step().reject_label ?? ''" (input)="patch.emit({ reject_label: val($event) || undefined })" />
      </div>
    </div>
    <div deskField id="step-expires" label="Expires after (hours, optional)" hint="Rejecting, or letting it expire, takes the route rejected.">
      <input id="step-expires" class="input" type="number" min="1" max="720" [value]="step().expires_after_hours ?? ''" (input)="patch.emit({ expires_after_hours: optNumber(val($event)) })" />
    </div>
  `,
})
export class AskFields {
  readonly step = input.required<AskStep>();
  readonly suggest = input.required<TemplateSuggestion[]>();
  readonly patch = output<Partial<Step>>();
  protected readonly val = val;
  protected readonly optNumber = optNumber;
}

@Component({
  selector: 'div[deskWaitFields]',
  imports: [Field],
  changeDetection: ChangeDetectionStrategy.OnPush,
  encapsulation: ViewEncapsulation.None,
  host: { style: 'display: contents' },
  template: `
    <fieldset class="field">
      <legend>Wait</legend>
      <label class="auto-check"><input type="radio" name="wait-mode" [checked]="!byTime()" (change)="patch.emit({ minutes: 60, until: undefined })" /> For a number of minutes</label>
      <label class="auto-check"><input type="radio" name="wait-mode" [checked]="byTime()" (change)="patch.emit({ until: '08:00', minutes: undefined })" /> Until a time of day</label>
    </fieldset>
    @if (byTime()) {
      <div deskField id="step-until" label="Until" hint="In the automation's timezone: its first schedule's, else this computer's.">
        <input id="step-until" class="input" type="time" [value]="step().until ?? ''" (input)="patch.emit({ until: val($event) })" />
      </div>
    } @else {
      <div deskField id="step-minutes" label="Minutes" hint="At most a week.">
        <input id="step-minutes" class="input" type="number" min="1" [attr.max]="maxMinutes" [value]="step().minutes ?? ''" (input)="patch.emit({ minutes: optNumber(val($event)) })" />
      </div>
    }
  `,
})
export class WaitFields {
  readonly step = input.required<WaitStep>();
  readonly patch = output<Partial<Step>>();
  protected readonly val = val;
  protected readonly optNumber = optNumber;
  protected readonly maxMinutes = MAX_WAIT_MINUTES;
  protected readonly byTime = computed(() => this.step().until !== undefined);
}

@Component({
  selector: 'div[deskAutomationFields]',
  imports: [Field, TemplateField],
  changeDetection: ChangeDetectionStrategy.OnPush,
  encapsulation: ViewEncapsulation.None,
  host: { style: 'display: contents' },
  template: `
    <div deskField id="step-automation" label="Automation to run" hint="Its result is its output step's outputs and folder.">
      <select id="step-automation" class="select" (change)="patch.emit({ automation: val($event), inputs: {} })">
        <option value="" [selected]="!step().automation">Choose an automation…</option>
        @for (n of names(); track n) {
          <option [value]="n" [selected]="n === step().automation">{{ n }}</option>
        }
        @if (step().automation && !names().includes(step().automation)) {
          <option [value]="step().automation" [selected]="true">{{ step().automation }}</option>
        }
      </select>
    </div>
    @for (i of inputs() ?? []; track i.key) {
      <div deskTemplateField [id]="'step-input-' + i.key" [label]="i.required ? i.label : i.label + ' (optional)'" [value]="step().inputs[i.key] ?? ''" [suggestions]="suggest()" [hint]="i.description" (valueChange)="setInput(i.key, $event)"></div>
    }
  `,
})
export class AutomationFields {
  readonly projectId = input.required<string>();
  readonly selfName = input.required<string>();
  readonly step = input.required<SubAutomationStep>();
  readonly suggest = input.required<TemplateSuggestion[]>();
  readonly patch = output<Partial<Step>>();
  protected readonly val = val;
  private readonly allNames = injectAutomationNames(() => this.projectId());
  protected readonly names = computed(() => this.allNames().filter((n) => n !== this.selfName()));
  protected readonly inputs = injectAutomationInputs(() => this.projectId(), () => this.step().automation);

  protected setInput(key: string, v: string): void {
    const rest = Object.fromEntries(Object.entries(this.step().inputs).filter(([k]) => k !== key));
    this.patch.emit({ inputs: v ? { ...rest, [key]: v } : rest });
  }
}

@Component({
  selector: 'div[deskTellFields]',
  imports: [ListEditor, TemplateField],
  changeDetection: ChangeDetectionStrategy.OnPush,
  encapsulation: ViewEncapsulation.None,
  host: { style: 'display: contents' },
  template: `
    <div deskTemplateField id="step-text" label="Message to Desk" [value]="step().text" [suggestions]="suggest()" [multiline]="true" [rows]="4" hint="Desk reads it at its next turn, as a message from this automation." (valueChange)="patch.emit({ text: $event })"></div>
    <fieldset deskListEditor id="step-attach" label="Files to attach" [values]="step().attach" placeholder="report.md" addLabel="Add file" [max]="20" hint="Globs of files in earlier steps' folders; Desk gets their paths." (valuesChange)="patch.emit({ attach: $event })"></fieldset>
  `,
})
export class TellFields {
  readonly step = input.required<TellDeskStep>();
  readonly suggest = input.required<TemplateSuggestion[]>();
  readonly patch = output<Partial<Step>>();
}

/** The fields of one step kind (spec §4). */
@Component({
  selector: 'div[deskStepKindFields]',
  imports: [AgentFields, AskFields, AutomationFields, ScriptFields, TellFields, WaitFields],
  changeDetection: ChangeDetectionStrategy.OnPush,
  encapsulation: ViewEncapsulation.None,
  host: { style: 'display: contents' },
  template: `
    @if (script(); as s) {
      <div deskScriptFields [projectId]="projectId()" [step]="s" [suggest]="suggest()" (patch)="patch.emit($event)"></div>
    } @else if (agent(); as s) {
      <div deskAgentFields [projectId]="projectId()" [sources]="sources()" [step]="s" [suggest]="suggest()" (patch)="patch.emit($event)"></div>
    } @else if (ask(); as s) {
      <div deskAskFields [step]="s" [suggest]="suggest()" (patch)="patch.emit($event)"></div>
    } @else if (wait(); as s) {
      <div deskWaitFields [step]="s" (patch)="patch.emit($event)"></div>
    } @else if (sub(); as s) {
      <div deskAutomationFields [projectId]="projectId()" [selfName]="selfName()" [step]="s" [suggest]="suggest()" (patch)="patch.emit($event)"></div>
    } @else if (tell(); as s) {
      <div deskTellFields [step]="s" [suggest]="suggest()" (patch)="patch.emit($event)"></div>
    }
  `,
})
export class StepKindFields {
  readonly projectId = input.required<string>();
  readonly selfName = input.required<string>();
  readonly sources = input.required<Sources>();
  readonly step = input.required<Step>();
  readonly suggest = input.required<TemplateSuggestion[]>();
  readonly patch = output<Partial<Step>>();
  protected readonly script = computed(() => narrow(this.step(), 'script'));
  protected readonly agent = computed(() => narrow(this.step(), 'agent'));
  protected readonly ask = computed(() => narrow(this.step(), 'ask'));
  protected readonly wait = computed(() => narrow(this.step(), 'wait'));
  protected readonly sub = computed(() => narrow(this.step(), 'automation'));
  protected readonly tell = computed(() => narrow(this.step(), 'tell_desk'));
}

/** The step as its kind's type, or null (templates cannot narrow a signal's value). */
function narrow<K extends Step['kind']>(step: Step, kind: K): Extract<Step, { kind: K }> | null {
  return step.kind === kind ? (step as Extract<Step, { kind: K }>) : null;
}
