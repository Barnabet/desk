import { ChangeDetectionStrategy, Component, ViewEncapsulation, inject, input, output, signal } from '@angular/core';
import type { SkillRef } from '@desk/ui-core';
import { Button } from '../components/button';
import { Field } from '../components/field';
import { Sheet } from '../components/sheet';
import { ToastService } from '../components/toast';
import { DeskBridge } from '../core/desk-bridge';

/**
 * Imports a skill folder the folder browser picked (SkillsScreen.tsx `ImportSheet`): globally or into one project,
 * under an optional new name. deskd copies it in; the original stays where it is.
 */
@Component({
  selector: 'div[deskImportSheet]',
  imports: [Button, Field, Sheet],
  changeDetection: ChangeDetectionStrategy.OnPush,
  encapsulation: ViewEncapsulation.None,
  host: { style: 'display: contents' },
  template: `
    <div deskSheet title="Import a skill" (close)="close.emit()">
      <p class="small">From <span class="mono">{{ path() }}</span>. The folder needs a SKILL.md. It's copied in; the original stays where it is.</p>
      <div deskField id="import-scope" label="Scope">
        <select id="import-scope" class="select" (change)="scope.set(val($event))">
          <option value="global" [selected]="scope() === 'global'">Global (every project)</option>
          @for (p of projects(); track p.id) {
            <option [value]="p.id" [selected]="scope() === p.id">{{ p.name }} only</option>
          }
        </select>
      </div>
      <div deskField id="import-name" label="Name (optional)" hint="Defaults to the folder's name.">
        <input id="import-name" class="input mono" [value]="name()" (input)="name.set(val($event))" />
      </div>
      <div class="sheet-footer">
        <button deskButton (click)="close.emit()">Cancel</button>
        <button deskButton variant="primary" [pending]="pending()" (click)="run()">Import</button>
      </div>
    </div>
  `,
})
export class ImportSheet {
  readonly path = input.required<string>();
  readonly projects = input.required<Array<{ id: string; name: string }>>();
  /** The skill the folder became (React's `onDone`). */
  readonly done = output<SkillRef>();
  readonly close = output<void>();

  private readonly bridge = inject(DeskBridge);
  private readonly toasts = inject(ToastService);
  protected readonly scope = signal('global');
  protected readonly name = signal('');
  protected readonly pending = signal(false);

  protected val(e: Event): string {
    return (e.target as HTMLInputElement | HTMLSelectElement).value;
  }

  protected async run(): Promise<void> {
    const scope = this.scope();
    const name = this.name().trim();
    const projectId = scope === 'global' ? undefined : scope;
    this.pending.set(true);
    try {
      const r = await this.bridge.call('skills.import', { ...(projectId ? { projectId } : {}), path: this.path(), ...(name ? { name } : {}) });
      // deskd's folder for the skill; its last segment is the skill's name (on Windows too).
      const imported = r.dir.split(/[\\/]/).filter(Boolean).pop() ?? name;
      this.toasts.toast({ tone: 'info', message: `Imported ${imported}.` });
      this.done.emit(projectId ? { scope: 'project', projectId, name: imported } : { scope: 'global', name: imported });
    } catch (err) {
      this.toasts.error(err);
    } finally {
      this.pending.set(false);
    }
  }
}
