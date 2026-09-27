import { ChangeDetectionStrategy, Component, inject, input, signal, ViewEncapsulation } from '@angular/core';
import { Button } from '../../components/button';
import { FileViewer } from '../../components/file-viewer';
import { ToastService } from '../../components/toast';
import { DeskBridge } from '../../core/desk-bridge';

/** Files of a run folder as buttons; one opens in the app's file viewer below them. */
@Component({
  selector: 'div[deskRunFileList]',
  imports: [Button, FileViewer],
  changeDetection: ChangeDetectionStrategy.OnPush,
  encapsulation: ViewEncapsulation.None,
  host: { class: 'auto-files' },
  template: `
    <ul class="auto-plain">
      @for (f of files(); track f.path) {
        <li><button type="button" class="link mono" (click)="show(f.path)">{{ f.label }}</button></li>
      }
    </ul>
    @if (open(); as o) {
      <div deskFileViewer [path]="o.path" [data]="o.data"><button deskButton size="sm" variant="ghost" (click)="open.set(null)">Close</button></div>
    }
  `,
})
export class RunFileList {
  readonly runId = input.required<string>();
  readonly files = input.required<Array<{ path: string; label: string }>>();
  private readonly bridge = inject(DeskBridge);
  private readonly toasts = inject(ToastService);
  protected readonly open = signal<{ path: string; data: Uint8Array } | null>(null);

  protected async show(path: string): Promise<void> {
    try {
      this.open.set({ path, data: await this.bridge.call('automations.file', { runId: this.runId(), path }) });
    } catch (err) {
      this.toasts.error(err);
    }
  }
}
