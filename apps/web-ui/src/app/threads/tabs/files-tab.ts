import { ChangeDetectionStrategy, Component, ViewEncapsulation, computed, effect, inject, input, model, signal, untracked } from '@angular/core';
import type { WorkspaceEntry } from '@desk/protocol';
import { bytes } from '@desk/ui-core';
import { EmptyState } from '../../components/empty-state';
import { FileViewer } from '../../components/file-viewer';
import { describeError, ToastService } from '../../components/toast';
import { DeskBridge, DeskCallError } from '../../core/desk-bridge';

type Listing = { status: 'loading' } | { status: 'ready'; entries: WorkspaceEntry[] } | { status: 'gone' } | { status: 'error'; message: string };
type Open = { path: string; data: Uint8Array } | null;

/** Browses the thread's workspace and shows one file at a time. `dir` is shared with the detail (Skill drafts opens a folder). */
@Component({
  selector: 'div[deskFilesTab]',
  imports: [EmptyState, FileViewer],
  changeDetection: ChangeDetectionStrategy.OnPush,
  encapsulation: ViewEncapsulation.None,
  host: { style: 'display: contents' },
  template: `
    @switch (list().status) {
      @case ('gone') {
        <div deskEmptyState title="The workspace is gone" body="It was removed when this thread was archived. Published files are still in the Library."></div>
      }
      @case ('error') {
        <div deskEmptyState title="Couldn't list the workspace" [body]="message()"></div>
      }
      @default {
        <div class="tab-body files-tab">
          <div class="files-browser">
            <nav class="crumbs" aria-label="Folder"><button type="button" class="link" (click)="dir.set('')">workspace</button>@for (c of crumbs(); track $index) {<span> / <button type="button" class="link" (click)="dir.set(crumbPath($index))">{{ c }}</button></span>}</nav>
            @if (entries(); as items) {
              @if (items.length) {
                <ul class="files-list">
                  @for (e of items; track e.path) {
                    <li>
                      <button type="button" class="files-entry" [class.current]="open()?.path === e.path" [attr.aria-busy]="loadingFile() === e.path ? 'true' : null" (click)="pick(e)"><span aria-hidden="true">{{ e.type === 'dir' ? '▸' : '·' }}</span><span class="grow mono">{{ e.name }}</span><span class="muted small">{{ e.type === 'dir' ? 'folder' : size(e.size) }}</span></button>
                    </li>
                  }
                </ul>
              } @else {
                <p class="muted">This folder is empty.</p>
              }
            } @else {
              <p class="muted">Loading…</p>
            }
          </div>
          <div class="files-pane">
            @if (open(); as o) {
              <div deskFileViewer [path]="o.path" [data]="o.data"></div>
            } @else {
              <p class="muted">Pick a file to view it.</p>
            }
          </div>
        </div>
      }
    }
  `,
})
export class FilesTab {
  readonly threadId = input.required<string>();
  /** The folder shown, relative to the workspace ('' is its root): the React `dir` and `onDir`. */
  readonly dir = model.required<string>();
  /** Changes whenever the thread's events do: the folder is listed again. */
  readonly version = input.required<string>();
  private readonly bridge = inject(DeskBridge);
  private readonly toasts = inject(ToastService);
  protected readonly list = signal<Listing>({ status: 'loading' });
  protected readonly open = signal<Open>(null);
  protected readonly loadingFile = signal<string | null>(null);
  protected readonly entries = computed(() => {
    const l = this.list();
    return l.status === 'ready' ? l.entries : null;
  });
  protected readonly message = computed(() => {
    const l = this.list();
    return l.status === 'error' ? l.message : null;
  });
  protected readonly crumbs = computed(() => (this.dir() ? this.dir().split('/') : []));
  protected readonly size = bytes;

  constructor() {
    effect((onCleanup) => {
      const id = this.threadId();
      const dir = this.dir();
      this.version();
      let live = true;
      onCleanup(() => {
        live = false;
      });
      untracked(() => {
        this.list.set({ status: 'loading' });
        this.bridge.call('threads.files', { id, ...(dir ? { path: dir } : {}) }).then(
          (entries) => {
            if (live) this.list.set({ status: 'ready', entries: [...entries].sort((a, b) => (a.type === b.type ? a.name.localeCompare(b.name) : a.type === 'dir' ? -1 : 1)) });
          },
          (err: unknown) => {
            if (!live) return;
            if (err instanceof DeskCallError && err.status === 409) this.list.set({ status: 'gone' });
            else this.list.set({ status: 'error', message: describeError(err).message });
          },
        );
      });
    });
  }

  protected crumbPath(i: number): string {
    return this.crumbs()
      .slice(0, i + 1)
      .join('/');
  }

  protected pick(e: WorkspaceEntry): void {
    if (e.type === 'dir') this.dir.set(e.path);
    else void this.openFile(e.path);
  }

  private async openFile(path: string): Promise<void> {
    this.loadingFile.set(path);
    try {
      const data = await this.bridge.call('threads.file', { id: this.threadId(), path });
      this.open.set({ path, data });
    } catch (err) {
      this.toasts.error(err);
    } finally {
      this.loadingFile.set(null);
    }
  }
}
