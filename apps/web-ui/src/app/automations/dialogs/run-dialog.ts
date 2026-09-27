import { booleanAttribute, ChangeDetectionStrategy, Component, computed, inject, input, linkedSignal, output, signal, untracked, ViewEncapsulation } from '@angular/core';
import type { AutomationDefinition, InputSpec } from '@desk/protocol';
import { initialValues, runInputs, type InputDraft } from '@desk/ui-core';
import { Button } from '../../components/button';
import { Field } from '../../components/field';
import { Sheet } from '../../components/sheet';
import { ToastService } from '../../components/toast';
import { DeskBridge } from '../../core/desk-bridge';

/** What Run now and Test need to know (an AutomationDetail fits). */
export type RunTarget = { id: string; name: string; title: string; definition: AutomationDefinition };

/** One input's control, by its type: a box, a list, a checkbox, or a path with Choose…. */
@Component({
  selector: 'div[deskInputField]',
  imports: [Button, Field],
  changeDetection: ChangeDetectionStrategy.OnPush,
  encapsulation: ViewEncapsulation.None,
  host: { style: 'display: contents' },
  template: `
    @switch (spec().type) {
      @case ('boolean') {
        <div class="field">
          <label class="auto-check"><input type="checkbox" [id]="fid()" [checked]="value() === true" (change)="valueChange.emit(checked($event))" /> {{ spec().label }}</label>
          @if (spec().description) {
            <p class="field-hint">{{ spec().description }}</p>
          }
        </div>
      }
      @case ('long_text') {
        <div deskField [id]="fid()" [label]="label()" [hint]="spec().description">
          <textarea class="textarea" rows="4" [id]="fid()" [value]="text()" (input)="valueChange.emit(val($event))"></textarea>
        </div>
      }
      @case ('choice') {
        <div deskField [id]="fid()" [label]="label()" [hint]="spec().description">
          <select class="select" [id]="fid()" (change)="valueChange.emit(val($event))">
            <option value="" [selected]="text() === ''">{{ spec().required ? 'Choose…' : 'None' }}</option>
            @for (opt of spec().options ?? []; track opt) {
              <option [value]="opt" [selected]="opt === text()">{{ opt }}</option>
            }
          </select>
        </div>
      }
      @case ('number') {
        <div deskField [id]="fid()" [label]="label()" [hint]="spec().description">
          <input class="input" type="number" [id]="fid()" [value]="text()" (input)="setNumber(val($event))" />
        </div>
      }
      @case ('file') {
        <div deskField [id]="fid()" [label]="label()" [hint]="spec().description ?? 'Desk copies the file into the run folder.'">
          <div class="auto-pick">
            <input class="input mono" placeholder="/path/to/file" [id]="fid()" [value]="text()" (input)="valueChange.emit(val($event))" />
            <button deskButton size="sm" (click)="pick('file')">Choose…</button>
          </div>
        </div>
      }
      @case ('folder') {
        <div deskField [id]="fid()" [label]="label()" [hint]="spec().description ?? 'Desk copies the folder into the run folder.'">
          <div class="auto-pick">
            <input class="input mono" placeholder="/path/to/folder" [id]="fid()" [value]="text()" (input)="valueChange.emit(val($event))" />
            <button deskButton size="sm" (click)="pick('folder')">Choose…</button>
          </div>
        </div>
      }
      @default {
        <div deskField [id]="fid()" [label]="label()" [hint]="spec().description">
          <input class="input" [id]="fid()" [type]="spec().type === 'url' ? 'url' : 'text'" [value]="text()" (input)="valueChange.emit(val($event))" />
        </div>
      }
    }
  `,
})
export class InputField {
  readonly spec = input.required<InputSpec>();
  readonly value = input.required<InputDraft>();
  /** React's onChange. */
  readonly valueChange = output<InputDraft>();

  private readonly bridge = inject(DeskBridge);
  private readonly toasts = inject(ToastService);
  protected readonly fid = computed(() => `run-input-${this.spec().key}`);
  protected readonly label = computed(() => (this.spec().required ? this.spec().label : `${this.spec().label} (optional)`));
  protected readonly text = computed(() => String(this.value()));

  protected val(e: Event): string {
    return (e.target as HTMLInputElement).value;
  }

  protected checked(e: Event): boolean {
    return (e.target as HTMLInputElement).checked;
  }

  protected setNumber(v: string): void {
    this.valueChange.emit(v === '' ? '' : Number(v));
  }

  protected async pick(kind: 'file' | 'folder'): Promise<void> {
    try {
      const path = kind === 'file' ? await this.bridge.call('app.pickFile', { purpose: 'automation-input' }) : await this.bridge.call('app.pickFolder', { purpose: 'automation-input' });
      if (path) this.valueChange.emit(path);
    } catch (err) {
      this.toasts.error(err);
    }
  }
}

/** Run now or Test (spec §8.4): a form generated from the automation's inputs. */
@Component({
  selector: 'div[deskRunDialog]',
  imports: [Button, InputField, Sheet],
  changeDetection: ChangeDetectionStrategy.OnPush,
  encapsulation: ViewEncapsulation.None,
  host: { style: 'display: contents' },
  template: `
    <div deskSheet [title]="(test() ? 'Test ' : 'Run ') + target().title" (close)="close.emit()">
      @if (test()) {
        <p class="muted small">{{ testNote() }}</p>
      }
      @for (i of inputs(); track i.key) {
        <div deskInputField [spec]="i" [value]="values()[i.key] ?? ''" (valueChange)="setValue(i.key, $event)"></div>
      } @empty {
        <p class="muted">This automation takes no inputs.</p>
      }
      <div class="sheet-footer">
        <button deskButton (click)="close.emit()">Cancel</button>
        <button deskButton variant="primary" [pending]="pending()" [disabled]="!ready()" (click)="start()">{{ test() ? 'Start test' : 'Run' }}</button>
      </div>
    </div>
  `,
})
export class RunDialog {
  readonly target = input.required<RunTarget>();
  readonly test = input(false, { transform: booleanAttribute });
  readonly close = output<void>();
  /** React's onStarted: the new run's id. */
  readonly started = output<string>();

  private readonly bridge = inject(DeskBridge);
  private readonly toasts = inject(ToastService);
  protected readonly inputs = computed(() => this.target().definition.inputs);
  /** The form's values, from the inputs' defaults once (a refreshed target keeps what was typed). */
  protected readonly values = linkedSignal(() => untracked(() => initialValues(this.inputs())));
  protected readonly ready = computed(() => runInputs(this.inputs(), this.values()));
  protected readonly pending = signal(false);
  protected readonly testNote = computed(
    () => `A test is a real run of the current version. Scripts see DESK_TEST=1 and can do a dry run, files publish under automations/${this.target().name}/tests/, and agent and Tell Desk steps act as usual.`,
  );

  protected setValue(key: string, v: InputDraft): void {
    this.values.update((s) => ({ ...s, [key]: v }));
  }

  protected async start(): Promise<void> {
    const ready = this.ready();
    if (!ready) return;
    this.pending.set(true);
    try {
      const { run_id } = await this.bridge.call('automations.run', { id: this.target().id, req: { inputs: ready, test: this.test() } });
      this.started.emit(run_id);
    } catch (err) {
      this.toasts.error(err);
      this.pending.set(false);
    }
  }
}
