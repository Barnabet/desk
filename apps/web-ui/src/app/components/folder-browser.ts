import { ChangeDetectionStrategy, Component, ViewEncapsulation, computed, inject, input, output, signal, type OnInit } from '@angular/core';
import type { DirListing } from '@desk/web-server/contract';
import { DeskBridge, DeskCallError, type FolderPurpose } from '../core/desk-bridge';
import { Button } from './button';
import { Field } from './field';
import { Sheet } from './sheet';
import { describeError } from './toast';

/** A full path on macOS, Linux or Windows. */
export const isAbsolutePath = (p: string): boolean => /^(?:\/|[A-Za-z]:[\\/]|\\\\)/.test(p);

/**
 * The web's folder dialog (spec §3, §4.12): folders under home and the projects' sources through `fs.listDirs`, or a
 * typed path. A typed absolute path it may not list is chosen as typed, for deskd to check. App shows it while
 * `DeskBridge.folderRequest()` is set and hands `picked` to `DeskBridge.answerFolder` (W0d.7). With `file`, it answers
 * `app.pickFile`: files are listed too, and one is chosen.
 */
@Component({
  selector: 'div[deskFolderBrowser]',
  encapsulation: ViewEncapsulation.None,
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [Button, Field, Sheet],
  // The Sheet moves its backdrop into document.body; this host only holds it, so it takes no box of its own.
  host: { style: 'display: contents' },
  styles: `
    .folder-path {
      display: flex;
      align-items: center;
      gap: 8px;
    }
    .folder-path > .field {
      flex: 1;
    }
    .folder-list {
      max-height: 260px;
      overflow: auto;
    }
    .folder-list li {
      justify-content: flex-start;
    }
    .folder-list .link {
      text-align: left;
      text-decoration: none;
    }
    .folder-list .link:hover {
      text-decoration: underline;
    }
  `,
  template: `
    <div deskSheet [title]="heading()" [width]="560" (close)="picked.emit(null)">
      <form class="folder-path" novalidate (submit)="go($event)">
        <div deskField id="folder-path" [label]="file() ? 'Path' : 'Folder'" [hint]="file() ? 'Type a full path, or click a file below.' : 'Type a full path, or ~ for your home folder.'">
          <input id="folder-path" class="input mono" autocomplete="off" spellcheck="false" [value]="typed()" (input)="typed.set(val($event))" />
        </div>
        <button deskButton type="submit" size="sm" [pending]="loading()">Go</button>
      </form>
      <div class="actions">
        <button deskButton size="sm" variant="ghost" [disabled]="!listing()?.parent" (click)="up()">Up</button>
        <label class="source-write"><input type="checkbox" [checked]="hidden()" (change)="toggleHidden($event)" /> {{ file() ? 'Show hidden files and folders' : 'Show hidden folders' }}</label>
      </div>
      @if (listing(); as l) {
        @if (l.dirs.length) {
          <ul class="sources folder-list" [attr.aria-label]="'Folders in ' + l.path">
            @for (d of l.dirs; track d.path) {
              <li><button type="button" class="link" (click)="list(d.path)">{{ d.name }}</button></li>
            }
          </ul>
        } @else {
          <p class="field-hint">No folders here.</p>
        }
        @if (file()) {
          @let files = l.files ?? [];
          @if (files.length) {
            <ul class="sources folder-list" [attr.aria-label]="'Files in ' + l.path">
              @for (f of files; track f.path) {
                <li><button type="button" class="link mono" [attr.aria-pressed]="typed() === f.path" (click)="pickFile(f.path)">{{ f.name }}</button></li>
              }
            </ul>
          } @else {
            <p class="field-hint">No files here.</p>
          }
        }
      }
      @if (error()) {
        <p class="field-error" role="alert">{{ error() }}</p>
      }
      <div class="actions">
        <button deskButton variant="primary" [pending]="loading() || checking()" (click)="choose()">{{ file() ? 'Choose this file' : 'Choose this folder' }}</button>
        <button deskButton (click)="picked.emit(null)">Cancel</button>
      </div>
    </div>
  `,
})
export class FolderBrowser implements OnInit {
  readonly purpose = input.required<FolderPurpose>();
  /** File mode (`app.pickFile`): files are listed, and one is chosen. */
  readonly file = input(false);
  /** The chosen folder, or null when the sheet closes without one. */
  readonly picked = output<string | null>();

  private readonly bridge = inject(DeskBridge);
  protected readonly heading = computed(() => (this.file() ? 'Choose a file' : this.purpose() === 'skill-import' ? 'Choose a skill folder' : 'Choose a folder'));
  protected readonly listing = signal<DirListing | null>(null);
  protected readonly typed = signal('');
  protected readonly hidden = signal(false);
  protected readonly loading = signal(false);
  /** Choose is checking a typed folder with deskd. */
  protected readonly checking = signal(false);
  protected readonly error = signal<string | null>(null);
  /** Only the latest listing lands, whatever order the answers come back in. */
  private seq = 0;

  ngOnInit(): void {
    this.hidden.set(this.purpose() === 'skill-import');
    void this.list(undefined);
  }

  protected val(e: Event): string {
    return (e.target as HTMLInputElement).value;
  }

  protected async list(path: string | undefined): Promise<void> {
    const n = ++this.seq;
    this.loading.set(true);
    this.error.set(null);
    try {
      const l = await this.bridge.call('fs.listDirs', { ...(path ? { path } : {}), hidden: this.hidden(), ...(this.file() ? { files: true } : {}) });
      if (n !== this.seq) return;
      this.listing.set(l);
      this.typed.set(l.path);
    } catch (err) {
      if (n === this.seq) this.error.set(describeError(err).message);
    } finally {
      if (n === this.seq) this.loading.set(false);
    }
  }

  protected go(e: Event): void {
    e.preventDefault();
    const path = this.typed().trim();
    if (path) void this.list(path);
  }

  protected up(): void {
    const parent = this.listing()?.parent;
    if (parent) void this.list(parent);
  }

  protected toggleHidden(e: Event): void {
    this.hidden.set((e.target as HTMLInputElement).checked);
    void this.list(this.listing()?.path);
  }

  /** File mode: a click puts the file's path in the box; Choose returns it. */
  protected pickFile(path: string): void {
    this.typed.set(path);
    this.error.set(null);
  }

  protected async choose(): Promise<void> {
    if (this.file()) {
      // A listed file's path, or a typed full path, which deskd checks when the run starts (it copies the file).
      const typed = this.typed().trim();
      if (typed && isAbsolutePath(typed) && typed !== this.listing()?.path) this.picked.emit(typed);
      else this.error.set('Pick a file in the list, or type its full path.');
      return;
    }
    // Pending while a listing lands (the folder on screen is about to change) or a typed folder is checked (one answer).
    if (this.loading() || this.checking()) return;
    const typed = this.typed().trim();
    const shown = this.listing();
    if (shown && (!typed || typed === shown.path)) {
      this.picked.emit(shown.path);
      return;
    }
    if (!typed) return;
    this.error.set(null);
    this.checking.set(true);
    try {
      const l = await this.bridge.call('fs.listDirs', { path: typed, hidden: this.hidden() });
      this.picked.emit(l.path);
    } catch (err) {
      // Outside home and the sources the browser may not list it, but deskd checks every folder it is given (spec §4.7).
      if (err instanceof DeskCallError && err.code === 'not_allowed' && isAbsolutePath(typed)) this.picked.emit(typed);
      else this.error.set(describeError(err).message);
    } finally {
      this.checking.set(false);
    }
  }
}
