import { ChangeDetectionStrategy, Component, ViewEncapsulation, computed, inject, input, output, signal, type OnInit } from '@angular/core';
import { Button } from '../components/button';
import { Field } from '../components/field';
import { Sheet } from '../components/sheet';
import { ToastService } from '../components/toast';
import { DeskBridge } from '../core/desk-bridge';
import { RouteService } from '../core/route.service';

/** Sends Desk a message about a skill: build a new one, or refine a named one. Global skills need a project whose Desk does the work (AskDesk.tsx). */
@Component({
  selector: 'div[deskAskDesk]',
  imports: [Button, Field, Sheet],
  changeDetection: ChangeDetectionStrategy.OnPush,
  encapsulation: ViewEncapsulation.None,
  host: { style: 'display: contents' },
  template: `
    <div deskSheet [title]="sheetTitle()" (close)="close.emit()">
      @if (projects().length) {
        <div deskField id="ask-project" label="Which project's Desk" hint="Desk has a thread draft and test it, then installs it.">
          <select id="ask-project" class="select" (change)="projectId.set(val($event))">
            @for (p of projects(); track p.id) {
              <option [value]="p.id" [selected]="projectId() === p.id">{{ p.name }}</option>
            }
          </select>
        </div>
        <div deskField id="ask-text" label="Message">
          <textarea id="ask-text" class="textarea" rows="4" [value]="text()" (input)="text.set(val($event))"></textarea>
        </div>
      } @else {
        <p>Create a project first; Desk works on skills from inside a project.</p>
      }
      <div class="sheet-footer">
        <button deskButton (click)="close.emit()">Cancel</button>
        <button deskButton variant="primary" [pending]="pending()" [disabled]="!projectId() || text().trim().length < 12" (click)="send()">Send to Desk</button>
      </div>
    </div>
  `,
})
export class AskDesk implements OnInit {
  /** The skill to refine; absent to ask for a new one. */
  readonly skillName = input<string>();
  readonly projects = input.required<Array<{ id: string; name: string }>>();
  readonly defaultProjectId = input<string>();
  readonly close = output<void>();

  private readonly bridge = inject(DeskBridge);
  private readonly routes = inject(RouteService);
  private readonly toasts = inject(ToastService);
  protected readonly projectId = signal('');
  protected readonly text = signal('');
  protected readonly pending = signal(false);
  protected readonly sheetTitle = computed(() => {
    const name = this.skillName();
    return name ? `Refine ${name} with Desk` : 'Ask Desk for a new skill';
  });

  /** React's useState initial values: taken from the inputs once. */
  ngOnInit(): void {
    this.projectId.set(this.defaultProjectId() ?? this.projects()[0]?.id ?? '');
    const name = this.skillName();
    this.text.set(name ? `Refine the skill "${name}": ` : 'Build a new skill that ');
  }

  protected val(e: Event): string {
    return (e.target as HTMLTextAreaElement | HTMLSelectElement).value;
  }

  protected async send(): Promise<void> {
    const id = this.projectId();
    this.pending.set(true);
    try {
      await this.bridge.call('projects.send', { id, text: this.text().trim() });
      this.toasts.toast({ tone: 'info', message: 'Sent to Desk.' });
      this.close.emit();
      this.routes.navigate({ name: 'project', id, tab: 'conversation' });
    } catch (err) {
      this.toasts.error(err);
    } finally {
      this.pending.set(false);
    }
  }
}
