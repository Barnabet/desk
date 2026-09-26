import { ChangeDetectionStrategy, Component, ViewEncapsulation, inject, input, output, signal } from '@angular/core';
import { Button } from '../components/button';
import { Field } from '../components/field';
import { ToastService } from '../components/toast';
import { DeskBridge, DeskCallError } from '../core/desk-bridge';
import { injectModels } from '../settings/models';
import { DEFAULT_STYLE, SettingsFields, type WorkingStyle } from '../settings/settings-fields';

/**
 * Name, goal, instructions, source folders (the folder browser) and, under More options, how Desk works. Used by
 * onboarding and the new-project sheet (ProjectForm.tsx).
 */
@Component({
  selector: 'form[deskProjectForm]',
  encapsulation: ViewEncapsulation.None,
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [Button, Field, SettingsFields],
  host: { class: 'sheet-body', novalidate: '', '(submit)': 'submit($event)' },
  template: `
    <div deskField id="project-name" label="Name" [error]="nameError()">
      <input id="project-name" class="input" placeholder="Onboarding revamp" [value]="name()" (input)="name.set(val($event))" />
    </div>
    <div deskField id="project-goal" label="Goal" hint="What Desk should work toward. You can refine it later.">
      <textarea id="project-goal" class="textarea" placeholder="Relaunch onboarding next month" [value]="goal()" (input)="goal.set(val($event))"></textarea>
    </div>
    <div deskField id="project-instructions" label="Standing instructions (optional)">
      <textarea id="project-instructions" class="textarea" [value]="instructions()" (input)="instructions.set(val($event))"></textarea>
    </div>
    <div class="field">
      <span class="eyebrow">Sources</span>
      @if (sources().length) {
        <ul class="sources">
          @for (s of sources(); track s) {
            <li>
              <span>{{ s }}</span>
              <button deskButton size="sm" variant="ghost" [attr.aria-label]="'Remove ' + s" (click)="remove(s)">Remove</button>
            </li>
          }
        </ul>
      } @else {
        <p class="field-hint">Folders or git repositories Desk and its threads can read. Optional.</p>
      }
      <div>
        <button deskButton size="sm" (click)="pick()">Add folder…</button>
      </div>
    </div>
    <details class="new-project-more" [open]="moreOpen()" (toggle)="moreOpen.set(isOpen($event))">
      <summary>More options</summary>
      @if (moreOpen()) {
        <div deskSettingsFields idPrefix="new-project" [value]="style()" [models]="models()" (changed)="patchStyle($event)"></div>
      }
    </details>
    @if (formError()) {
      <p class="field-error" role="alert">{{ formError() }}</p>
    }
    <div class="actions">
      <button deskButton type="submit" variant="primary" [pending]="pending()">{{ submitLabel() }}</button>
      @if (cancelable()) {
        <button deskButton (click)="cancelled.emit()">Cancel</button>
      }
    </div>
  `,
})
export class ProjectForm {
  readonly submitLabel = input('Create project');
  /** Shows Cancel (React shows it when `onCancel` is passed). */
  readonly cancelable = input(false);
  /** The new project's id. */
  readonly created = output<string>();
  readonly cancelled = output<void>();

  private readonly bridge = inject(DeskBridge);
  private readonly toasts = inject(ToastService);
  protected readonly models = injectModels();
  protected readonly name = signal('');
  protected readonly goal = signal('');
  protected readonly instructions = signal('');
  protected readonly sources = signal<string[]>([]);
  protected readonly nameError = signal<string | null>(null);
  protected readonly formError = signal<string | null>(null);
  protected readonly pending = signal(false);
  protected readonly style = signal<WorkingStyle>(DEFAULT_STYLE);
  protected readonly moreOpen = signal(false);

  protected val(e: Event): string {
    return (e.target as HTMLInputElement | HTMLTextAreaElement).value;
  }

  protected isOpen(e: Event): boolean {
    return (e.target as HTMLDetailsElement).open;
  }

  protected patchStyle(patch: Partial<WorkingStyle>): void {
    this.style.update((s) => ({ ...s, ...patch }));
  }

  protected remove(path: string): void {
    this.sources.update((list) => list.filter((x) => x !== path));
  }

  protected async pick(): Promise<void> {
    try {
      const path = await this.bridge.call('app.pickFolder', { purpose: 'source' });
      if (path && !this.sources().includes(path)) this.sources.update((list) => [...list, path]);
    } catch (err) {
      this.toasts.error(err);
    }
  }

  protected async submit(e: Event): Promise<void> {
    e.preventDefault();
    const name = this.name().trim();
    if (!name) {
      this.nameError.set('Give the project a name.');
      return;
    }
    this.nameError.set(null);
    this.formError.set(null);
    this.pending.set(true);
    const instructions = this.instructions().trim();
    const sources = this.sources();
    const style = this.style();
    try {
      const created = await this.bridge.call('projects.create', {
        name,
        goal: this.goal().trim(),
        ...(instructions ? { instructions } : {}),
        ...(sources.length ? { sources: sources.map((path) => ({ path })) } : {}),
        ...(JSON.stringify(style) !== JSON.stringify(DEFAULT_STYLE) ? { settings: style } : {}),
      });
      this.created.emit(created.project.id);
    } catch (err) {
      if (err instanceof DeskCallError && err.status === 400) this.formError.set(err.message);
      else this.toasts.error(err);
    } finally {
      this.pending.set(false);
    }
  }
}
