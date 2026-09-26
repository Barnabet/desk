import { ChangeDetectionStrategy, Component, ViewEncapsulation, inject, input, output, signal } from '@angular/core';
import { Button } from '../../components/button';
import { EmptyState } from '../../components/empty-state';
import { ToastService } from '../../components/toast';
import { DeskBridge } from '../../core/desk-bridge';

/** Skill drafts the thread submitted with its result. Threads can't install skills; Desk reviews and installs them. */
@Component({
  selector: 'div[deskSkillDraftsTab]',
  imports: [Button, EmptyState],
  changeDetection: ChangeDetectionStrategy.OnPush,
  encapsulation: ViewEncapsulation.None,
  host: { style: 'display: contents' },
  template: `
    @if (drafts().length) {
      <ul class="tab-body drafts">
        @for (d of drafts(); track d) {
          <li class="draft">
            <span class="mono grow">{{ d }}</span>
            <button deskButton size="sm" variant="ghost" (click)="browse.emit(d)">View files</button>
            <button deskButton size="sm" variant="primary" [pending]="asking() === d" (click)="ask(d)">Ask Desk to review and install</button>
          </li>
        }
      </ul>
    } @else {
      <div
        deskEmptyState
        title="No skill drafts"
        body="A thread can package what it learned as a skill draft (a folder with a SKILL.md). Drafts show up here for Desk to review and install."
      ></div>
    }
  `,
})
export class SkillDraftsTab {
  readonly projectId = input.required<string>();
  readonly threadTitle = input.required<string>();
  readonly drafts = input.required<string[]>();
  /** Opens the Files tab at that folder (React's `onBrowse`). */
  readonly browse = output<string>();
  private readonly bridge = inject(DeskBridge);
  private readonly toasts = inject(ToastService);
  protected readonly asking = signal<string | null>(null);

  protected async ask(dir: string): Promise<void> {
    this.asking.set(dir);
    try {
      await this.bridge.call('projects.send', { id: this.projectId(), text: `Please review the skill draft at ${dir} from the thread "${this.threadTitle()}" and install it if it's good.` });
      this.toasts.toast({ tone: 'info', message: 'Asked Desk to review the draft.' });
    } catch (err) {
      this.toasts.error(err);
    } finally {
      this.asking.set(null);
    }
  }
}
