import { ChangeDetectionStrategy, Component, DestroyRef, ViewEncapsulation, computed, effect, inject, input, linkedSignal, output, signal, untracked } from '@angular/core';
import type { SkillDetail } from '@desk/client';
import type { BuiltinSkillInfo } from '@desk/protocol';
import { bytes, href, type SkillRef } from '@desk/ui-core';
import { Button } from '../../components/button';
import { EmptyState } from '../../components/empty-state';
import { Field } from '../../components/field';
import { FileViewer } from '../../components/file-viewer';
import { SafeMarkdown } from '../../components/safe-markdown';
import { Sheet } from '../../components/sheet';
import { describeError, ToastService } from '../../components/toast';
import { DeskBridge } from '../../core/desk-bridge';
import { SkillsRefresh } from '../skills-refresh';
import { BuiltinRuntime, BuiltinSwitch } from './builtin-group';

type Tab = 'overview' | 'instructions' | 'files';
const TABS: Tab[] = ['overview', 'instructions', 'files'];
type OpenFile = { path: string; data: Uint8Array };

/**
 * One of Desk's built-in skills: read-only instructions and files, its environment, the switch, and Duplicate, whose
 * sheet is React's private `DuplicateSheet` inlined (BuiltinPanel.tsx).
 */
@Component({
  selector: 'article[deskBuiltinPanel]',
  imports: [BuiltinRuntime, BuiltinSwitch, Button, EmptyState, Field, FileViewer, SafeMarkdown, Sheet],
  changeDetection: ChangeDetectionStrategy.OnPush,
  encapsulation: ViewEncapsulation.None,
  host: { class: 'card skill-panel', '[attr.aria-label]': "'Built-in skill ' + item().name" },
  template: `
    <div class="skill-panel-head">
      <h2 class="mono">{{ item().name }}</h2>
      <span class="skill-scope builtin">Built in</span>
      @if (!item().broken) {
        <button deskBuiltinSwitch [item]="item()" [label]="item().title + ' on'" (changed)="changed.emit()"></button>
      }
      <button type="button" class="icon-btn" aria-label="Close" (click)="close.emit()">✕</button>
    </div>
    <p class="skill-desc">{{ item().summary }}</p>
    @if (item().broken; as broken) {
      <p class="field-error" role="alert">This copy of Desk is damaged ({{ broken }}), so agents can't use it. Reinstall Desk to repair it.</p>
    } @else {
      <div class="builtin-runtime">
        <span deskBuiltinRuntime [item]="item()"></span>
        @if (item().runtime.state === 'failed') {
          <button deskButton size="sm" [pending]="busyHere('retry')" (click)="retry()">Retry</button>
        }
      </div>
    }
    @if (!item().enabled) {
      <p class="shadow-note">Turned off: agents never see it. Turn it back on with the switch.</p>
    }
    @if (item().shadowed_by === 'global') {
      <p class="shadow-note">Shadowed by your skill <a [href]="shadowHref()">{{ item().name }}</a>: agents use yours. Delete it to bring the built-in back.</p>
    }
    @if (error(); as err) {
      <div deskEmptyState title="Couldn't load this skill" [body]="err"></div>
    } @else if (detail(); as d) {
      <div class="tabs" role="tablist" aria-label="Skill">
        @for (t of tabs; track t) {
          <button type="button" role="tab" [attr.aria-selected]="tab() === t" (click)="tab.set(t)">{{ t === 'overview' ? 'Overview' : t === 'instructions' ? 'Instructions' : 'Files · ' + d.files.length }}</button>
        }
      </div>
      <div class="skill-tab" role="tabpanel">
        @switch (tab()) {
          @case ('overview') {
            <p class="small">{{ d.description }}</p>
            @if (item().caveats.length) {
              <div>
                <span class="label">Good to know</span>
                <ul class="builtin-caveats">
                  @for (c of item().caveats; track c) {
                    <li class="small">{{ c }}</li>
                  }
                </ul>
              </div>
            }
            <p class="muted small">{{ item().scripts }} scripts. Desk sets up their Python environment the first time an agent uses the skill, and updates it with Desk.</p>
          }
          @case ('instructions') {
            <div deskSafeMarkdown [text]="d.instructions"></div>
          }
          @default {
            <div class="skill-files">
              <ul class="files-list">
                @for (f of d.files; track f.path) {
                  <li>
                    <button type="button" class="files-entry" [class.current]="file()?.path === f.path" (click)="openFile(f.path)">
                      <span class="grow mono">{{ f.path }}</span>
                      <span class="muted small">{{ bytes(f.size) }}</span>
                    </button>
                  </li>
                }
              </ul>
              @if (file(); as f) {
                <div deskFileViewer [path]="f.path" [data]="f.data"></div>
              } @else {
                <p class="muted small">Pick a file to view it.</p>
              }
            </div>
          }
        }
      </div>
    } @else {
      <p class="muted">Loading…</p>
    }
    <div class="skill-actions">
      <button deskButton variant="primary" (click)="openDuplicate()">Duplicate to my skills</button>
      <span class="grow"></span>
    </div>
    @if (duplicating()) {
      <div deskSheet [title]="'Duplicate ' + item().name" (close)="dismiss()">
        <p class="small">Your copy is an ordinary skill you can edit. Agents use it instead of the built-in until you delete it; its scripts keep using the built-in's Python environment.</p>
        <div deskField id="duplicate-scope" label="Where">
          <select id="duplicate-scope" class="select" (change)="target.set(val($event))">
            <option value="global" [selected]="target() === 'global'">My skills (every project)</option>
            @for (p of projects(); track p.id) {
              <option [value]="p.id" [selected]="target() === p.id">{{ p.name }} only</option>
            }
          </select>
        </div>
        <div class="sheet-footer">
          <button deskButton [disabled]="busyHere('duplicate')" (click)="dismiss()">Cancel</button>
          <button deskButton variant="primary" [pending]="busyHere('duplicate')" (click)="duplicate()">Duplicate</button>
        </div>
      </div>
    }
  `,
})
export class BuiltinPanel {
  readonly item = input.required<BuiltinSkillInfo>();
  readonly projects = input.required<Array<{ id: string; name: string }>>();
  /** The copy Duplicate made (React's `onDuplicated`). */
  readonly duplicated = output<SkillRef>();
  readonly changed = output<void>();
  readonly close = output<void>();

  private readonly bridge = inject(DeskBridge);
  private readonly toasts = inject(ToastService);
  /** The skills screen, told directly about a call that lands after this panel closed. */
  private readonly screen = inject(SkillsRefresh, { optional: true });
  protected readonly tabs = TABS;
  protected readonly bytes = bytes;
  /** Set once the panel is gone (the route left this built-in while a call ran): its outputs then reach no one. */
  private closed = false;

  private readonly name = computed(() => this.item().name);
  /** Back to Overview, with no file open, whenever another built-in opens (React's effect on the name). */
  protected readonly tab = linkedSignal<string, Tab>({ source: this.name, computation: () => 'overview' });
  protected readonly file = linkedSignal<string, OpenFile | null>({ source: this.name, computation: () => null });
  protected readonly detail = signal<SkillDetail | null>(null);
  protected readonly error = signal<string | null>(null);
  /** The Duplicate sheet; another built-in opening closes it, so it never duplicates that one. */
  protected readonly duplicating = linkedSignal<string, boolean>({ source: this.name, computation: () => false });
  protected readonly target = signal('global');
  /** Every call running, as `<name>|<what>` (`retry`, `duplicate`): each built-in's buttons show only their own. */
  private readonly busy = signal<ReadonlySet<string>>(new Set());
  protected readonly shadowHref = computed(() => href({ name: 'skills', skill: `global:${this.name()}` }));

  constructor() {
    inject(DestroyRef).onDestroy(() => (this.closed = true));
    // React's effect on [item.name]; a late answer for another built-in is dropped. The call runs untracked, so a
    // signal the bridge reads (signedOut on a sign-in retry) never fetches the built-in again.
    effect((onCleanup) => {
      const name = this.name();
      let live = true;
      onCleanup(() => (live = false));
      this.error.set(null);
      untracked(() => this.bridge.call('builtins.get', { name }))
        .then((d) => {
          if (live) this.detail.set(d);
        })
        .catch((err: unknown) => {
          if (live) this.error.set(describeError(err).message);
        });
    });
  }

  protected val(e: Event): string {
    return (e.target as HTMLSelectElement).value;
  }

  /** Whether `what` (`retry`, `duplicate`) is running on the open built-in. */
  protected busyHere(what: string): boolean {
    return this.busy().has(`${this.name()}|${what}`);
  }

  /** Cancel, Escape and the backdrop do nothing while the copy is being made, so `duplicated` still reaches the screen. */
  protected dismiss(): void {
    if (!this.busyHere('duplicate')) this.duplicating.set(false);
  }

  protected async openFile(path: string): Promise<void> {
    const name = this.name();
    try {
      const data = await this.bridge.call('builtins.file', { name, path });
      // Another built-in opened meanwhile: this file is not one of its files.
      if (this.name() === name) this.file.set({ path, data });
    } catch (err) {
      if (this.name() === name) this.toasts.error(err);
    }
  }

  protected async retry(): Promise<void> {
    const name = this.name();
    const done = this.mark(`${name}|retry`);
    try {
      await this.bridge.call('builtins.retry', { name });
      // It did happen, so its toast and `changed` come even when another built-in opened, or the panel closed, meanwhile.
      this.toasts.toast({ tone: 'info', message: `Setting up ${name} again.` });
      this.landed();
    } catch (err) {
      this.toasts.error(err);
    } finally {
      done();
    }
  }

  /** React mounts a fresh DuplicateSheet each time, so the target starts at My skills again. */
  protected openDuplicate(): void {
    this.target.set('global');
    this.duplicating.set(true);
  }

  protected async duplicate(): Promise<void> {
    const name = this.name();
    const target = this.target();
    const projectId = target === 'global' ? undefined : target;
    const done = this.mark(`${name}|duplicate`);
    try {
      await this.bridge.call('builtins.duplicate', { name, ...(projectId ? { projectId } : {}) });
      this.toasts.toast({ tone: 'info', message: `Duplicated ${name}. Your copy is used instead of the built-in.` });
      if (!this.closed && this.name() === name) {
        this.duplicating.set(false);
        this.duplicated.emit(projectId ? { scope: 'project', projectId, name } : { scope: 'global', name });
      } else {
        // Another built-in opened, or the panel closed, meanwhile: the lists change, but opening the copy would leave
        // where the user went.
        this.landed();
      }
    } catch (err) {
      this.toasts.error(err);
    } finally {
      done();
    }
  }

  /** Something changed: `changed` while the panel is open, else the screen itself (never both). */
  private landed(): void {
    if (this.closed) this.screen?.changed();
    else this.changed.emit();
  }

  /** Marks a call (`<name>|<what>`) as running; the returned function clears only that mark. */
  private mark(entry: string): () => void {
    this.busy.update((b) => new Set(b).add(entry));
    return () =>
      this.busy.update((b) => {
        const next = new Set(b);
        next.delete(entry);
        return next;
      });
  }
}
