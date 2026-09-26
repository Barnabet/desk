import { ChangeDetectionStrategy, Component, ElementRef, ViewEncapsulation, computed, effect, inject, input, signal, viewChild } from '@angular/core';
import type { ArtifactKind } from '@desk/protocol';
import { clock, extOf, fileToBase64, filterLibrary, href, imageMime, libraryFromEvents, MAX_UPLOAD, originAgent, plural, type LibraryItem } from '@desk/ui-core';
import { Button } from '../components/button';
import { EmptyState } from '../components/empty-state';
import { FileViewer } from '../components/file-viewer';
import { ToastService } from '../components/toast';
import { DeskBridge } from '../core/desk-bridge';
import { RouteService } from '../core/route.service';
import { injectSession } from '../core/session.service';

const KINDS: Array<ArtifactKind | 'all'> = ['all', 'report', 'code', 'data', 'file', 'other'];

const kindLabel = (k: ArtifactKind | 'all'): string => (k === 'all' ? 'All' : k[0]!.toUpperCase() + k.slice(1));

function glyph(i: LibraryItem): string {
  if (imageMime(i.path)) return 'IMG';
  const ext = extOf(i.path);
  return ext ? ext.slice(0, 4).toUpperCase() : i.kind.slice(0, 3).toUpperCase();
}

type Preview = { status: 'loading' } | { status: 'ready'; data: Uint8Array } | { status: 'error'; message: string };

/** The project's library: a grid of published files with a preview, upload by drop or picker, and download (LibraryScreen.tsx). */
@Component({
  selector: 'div[deskLibraryScreen]',
  imports: [Button, EmptyState, FileViewer],
  changeDetection: ChangeDetectionStrategy.OnPush,
  encapsulation: ViewEncapsulation.None,
  host: {
    '[class]': 'hostClass()',
    '[class.dragging]': "view() === 'ready' && dragging()",
    '[class.with-preview]': "view() === 'ready' && !!file()",
    '(dragover)': 'onDragOver($event)',
    '(dragleave)': 'onDragLeave($event)',
    '(drop)': 'onDrop($event)',
  },
  template: `
    @switch (view()) {
      @case ('loading') {Loading…}
      @case ('error') {
        <div deskEmptyState title="Couldn't load this project" [body]="s().error"></div>
      }
      @default {
        <div class="library-main">
          <div class="knowledge-head">
            <h1 class="title">Library</h1>
            <span class="muted">{{ plural(items().length, 'file') }}. Threads publish here; so can you.</span>
            <span class="grow"></span>
            <button deskButton variant="primary" [pending]="uploading() > 0" (click)="picker.click()">Upload…</button>
            <input #picker type="file" multiple hidden data-testid="library-input" (change)="upload(picker.files)" />
          </div>
          <div class="knowledge-filters">
            <div class="segmented" role="group" aria-label="Kind">
              @for (k of kinds; track k) {
                <button type="button" [attr.aria-pressed]="kind() === k" (click)="kind.set(k)">{{ kindLabel(k) }}</button>
              }
            </div>
            <input class="knowledge-search" type="search" aria-label="Filter the library" placeholder="Filter by title or path" [value]="query()" (input)="query.set(val($event))" />
          </div>
          @if (shown().length) {
            <ul class="library-grid">
              @for (i of shown(); track i.path) {
                <li>
                  <button type="button" class="card library-card" [class.current]="i.path === file()" [attr.aria-pressed]="i.path === file()" (click)="select(i.path === file() ? null : i.path)">
                    <span class="library-glyph" [class]="'kind-' + i.kind" aria-hidden="true">{{ glyph(i) }}</span>
                    <span class="library-title">{{ i.title }}</span>
                    <span class="mono small library-path">{{ i.path }}</span>
                    <span class="small muted">{{ i.kind }} · {{ who(i.origin) }} · {{ clock(i.ts) }}</span>
                  </button>
                </li>
              }
            </ul>
          } @else if (items().length) {
            <p class="muted">Nothing matches.</p>
          } @else {
            <div deskEmptyState title="Nothing here yet" [body]="'Drop files anywhere on this page, or upload them. Desk and its threads can read everything in the Library.'"></div>
          }
          <p class="drop-hint muted small">{{ dragging() ? 'Drop to upload' : 'Drop files anywhere to upload them.' }}</p>
        </div>
        @if (file(); as f) {
          <aside class="library-preview" aria-label="Preview">
            @if (selected(); as sel) {
              <div class="library-meta">
                <h2>{{ sel.title }}</h2>
                @if (sel.description) {
                  <p class="small">{{ sel.description }}</p>
                }
                <p class="small muted">{{ sel.kind }} · published by&ngsp;@if (originAgent(sel.origin); as agent) {<a [href]="threadHref(agent)">{{ threadTitle()(agent) }}</a>} @else {you}&ngsp;at {{ clock(sel.ts) }}</p>
              </div>
            }
            @if (previewData(); as data) {
              <div deskFileViewer [path]="f" [data]="data">
                <button deskButton size="sm" variant="ghost" aria-label="Close preview" (click)="select(null)">✕</button>
              </div>
            } @else if (previewError() !== null) {
              <div deskEmptyState title="Couldn't open this file" [body]="previewError()"></div>
            } @else {
              <p class="muted">Loading…</p>
            }
          </aside>
        }
      }
    }
  `,
})
export class LibraryScreen {
  readonly projectId = input.required<string>();
  /** The previewed file (the route's `?file=`). */
  readonly file = input<string>();

  private readonly bridge = inject(DeskBridge);
  private readonly routes = inject(RouteService);
  private readonly toasts = inject(ToastService);
  private readonly picker = viewChild<ElementRef<HTMLInputElement>>('picker');
  protected readonly s = injectSession(this.projectId);
  private readonly events = computed(() => this.s().events);
  private readonly project = computed(() => this.s().project);

  protected readonly items = computed(() => libraryFromEvents(this.events()));
  protected readonly kind = signal<ArtifactKind | 'all'>('all');
  protected readonly query = signal('');
  protected readonly dragging = signal(false);
  protected readonly uploading = signal(0);
  private readonly preview = signal<Preview | null>(null);
  protected readonly shown = computed(() => filterLibrary(this.items(), this.kind(), this.query()));
  protected readonly selected = computed(() => {
    const f = this.file();
    return f ? this.items().find((i) => i.path === f) : undefined;
  });
  /** A republished file is fetched again. */
  private readonly version = computed(() => this.selected()?.eventId);
  protected readonly previewData = computed(() => {
    const p = this.preview();
    return p?.status === 'ready' ? p.data : null;
  });
  protected readonly previewError = computed(() => {
    const p = this.preview();
    return p?.status === 'error' ? p.message : null;
  });
  protected readonly threadTitle = computed(() => {
    const p = this.project();
    return (id: string): string => p?.threads.find((t) => t.id === id)?.title ?? (p?.desk?.id === id ? 'Desk' : 'a thread');
  });

  protected readonly view = computed<'loading' | 'error' | 'ready'>(() => {
    const status = this.s().status;
    return status === 'loading' ? 'loading' : status === 'ready' ? 'ready' : 'error';
  });
  protected readonly hostClass = computed(() => {
    const view = this.view();
    if (view === 'loading') return 'page muted';
    if (view === 'error') return 'page';
    return 'library';
  });

  protected readonly kinds = KINDS;
  protected readonly kindLabel = kindLabel;
  protected readonly glyph = glyph;
  protected readonly clock = clock;
  protected readonly plural = plural;
  protected readonly originAgent = originAgent;

  constructor() {
    effect((onCleanup) => {
      const projectId = this.projectId();
      const file = this.file();
      this.version();
      if (!file) {
        this.preview.set(null);
        return;
      }
      let live = true;
      this.preview.set({ status: 'loading' });
      this.bridge.call('library.file', { projectId, path: file }).then(
        (data) => {
          if (live) this.preview.set({ status: 'ready', data });
        },
        (err: unknown) => {
          if (live) this.preview.set({ status: 'error', message: err instanceof Error ? err.message : String(err) });
        },
      );
      onCleanup(() => {
        live = false;
      });
    });
  }

  protected val(e: Event): string {
    return (e.target as HTMLInputElement).value;
  }

  /** "you", or the publishing thread's title. */
  protected who(origin: string): string {
    const agent = originAgent(origin);
    return agent ? this.threadTitle()(agent) : 'you';
  }

  protected threadHref(agentId: string): string {
    return href({ name: 'project', id: this.projectId(), tab: 'threads', threadId: agentId });
  }

  /** Selection lives in the route (`?file=`), replaced rather than pushed. */
  protected select(path: string | null): void {
    this.routes.replace({ name: 'project', id: this.projectId(), tab: 'library', ...(path ? { file: path } : {}) });
  }

  protected async upload(files: FileList | File[] | null): Promise<void> {
    const list = Array.from(files ?? []);
    for (const f of list) {
      if (f.size > MAX_UPLOAD) {
        this.toasts.error(new Error(`${f.name} is larger than 25 MB.`));
        continue;
      }
      this.uploading.update((n) => n + 1);
      try {
        const a = await this.bridge.call('library.upload', { projectId: this.projectId(), file: { name: f.name, content_base64: await fileToBase64(f) } });
        if (list.length === 1) this.select(a.path);
      } catch (err) {
        this.toasts.error(err);
      } finally {
        this.uploading.update((n) => n - 1);
      }
    }
    if (list.length > 1) this.toasts.toast({ tone: 'info', message: `Uploaded ${plural(list.length, 'file')}.` });
    const picker = this.picker()?.nativeElement;
    if (picker) picker.value = '';
  }

  protected onDragOver(e: DragEvent): void {
    if (this.view() !== 'ready') return;
    e.preventDefault();
    this.dragging.set(true);
  }

  protected onDragLeave(e: DragEvent): void {
    if (e.currentTarget === e.target) this.dragging.set(false);
  }

  protected onDrop(e: DragEvent): void {
    if (this.view() !== 'ready') return;
    e.preventDefault();
    this.dragging.set(false);
    void this.upload(e.dataTransfer?.files ?? null);
  }
}
