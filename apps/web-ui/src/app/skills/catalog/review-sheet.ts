import { ChangeDetectionStrategy, Component, DestroyRef, ViewEncapsulation, computed, effect, inject, input, output, signal, untracked } from '@angular/core';
import type { CatalogEntry, CatalogItem, CatalogReview, ReviewWarningKind } from '@desk/protocol';
import { bytes, skillKey, type SkillRef } from '@desk/ui-core';
import { Button } from '../../components/button';
import { ExternalLink } from '../../components/external-link';
import { Field } from '../../components/field';
import { FileViewer } from '../../components/file-viewer';
import { Sheet } from '../../components/sheet';
import { describeError, ToastService } from '../../components/toast';
import { DeskBridge } from '../../core/desk-bridge';
import { RouteService } from '../../core/route.service';
import { SkillsRefresh } from '../skills-refresh';
import { actionFor, installRef, runtimePackages, runtimeWords, sourceLabel } from './data';
import { RuntimeLine } from './runtime-line';

const WARNING: Record<ReviewWarningKind, string> = {
  'exec-block': 'A command block. Some agents run these before reading the skill; Desk never does.',
  'pipe-to-shell': 'Downloads a script and runs it.',
  'base64-blob': 'A long run of encoded data.',
  'invisible-unicode': 'Invisible characters, which can hide text.',
  'paste-site': 'A link to a paste site.',
  'memory-write': 'Asks the agent to write to memory or instruction files.',
};

const encoder = new TextEncoder();

type OpenFile = { path: string; data: Uint8Array; line?: number };

/**
 * Everything to check before installing a catalog skill: the pinned source, the licence, every file (scripts marked),
 * anything worth a look, what Desk will set up, and where to install it. Then Install, with progress (ReviewSheet.tsx).
 */
@Component({
  selector: 'div[deskReviewSheet]',
  imports: [Button, ExternalLink, Field, FileViewer, RuntimeLine, Sheet],
  changeDetection: ChangeDetectionStrategy.OnPush,
  encapsulation: ViewEncapsulation.None,
  host: { style: 'display: contents', '[attr.id]': 'null' },
  template: `
    <div deskSheet [title]="title()" [width]="860" (close)="dismiss()">
      @if (error(); as err) {
        <p class="field-error" role="alert">Couldn't prepare this skill: {{ err }}</p>
      } @else if (ready(); as r) {
        <div class="review">
          <p class="review-summary">{{ r.entry.summary }}</p>
          <dl class="review-facts">
            <div>
              <dt>Source</dt>
              <dd>@if (sourceLink(); as s) {<span deskExternalLink [href]="s.url">{{ s.label }}</span>&ngsp;<span class="mono small muted">&#64; {{ s.sha }}</span>} @else {Written by Desk and shipped with the app}</dd>
            </div>
            <div>
              <dt>Licence</dt>
              <dd>{{ r.entry.license }}@if (r.review.license_text) {&ngsp;·&ngsp;<button type="button" class="link" [attr.aria-expanded]="showLicense()" (click)="showLicense.set(!showLicense())">{{ showLicense() ? 'Hide text' : 'Read it' }}</button>}</dd>
            </div>
            <div>
              <dt>Desk sets up</dt>
              <dd>{{ runtimeWords(r.entry) }}@if (packages().length) {<span class="mono small muted"> · {{ packages().join(', ') }}</span>}</dd>
            </div>
            <div>
              <dt>Pinned</dt>
              <dd>{{ r.review.files.length }} files · {{ bytes(r.entry.bytes) }} · checked against <span class="mono small">{{ r.entry.digest.slice(7, 19) }}</span></dd>
            </div>
          </dl>
          @if (showLicense() && r.review.license_text) {
            <pre class="review-license">{{ r.review.license_text }}</pre>
          }
          @if (r.entry.caveats.length) {
            <ul class="review-caveats" aria-label="Good to know">
              @for (c of r.entry.caveats; track c) {
                <li>{{ c }}</li>
              }
            </ul>
          }
          @if (r.review.warnings.length) {
            <section class="review-warnings" aria-label="Worth a look">
              <h3>Worth a look · {{ r.review.warnings.length }}</h3>
              <p class="small muted">Automatic checks found these. They are often harmless; open each one to see it in context.</p>
              <ul>
                @for (w of r.review.warnings; track $index) {
                  <li><button type="button" class="link" (click)="openFile(w.file, w.line)">{{ w.file }}:{{ w.line }}</button>&ngsp;<span class="small">{{ warning[w.kind] }}</span><code class="review-excerpt">{{ w.excerpt }}</code></li>
                }
              </ul>
            </section>
          } @else {
            <p class="small muted">The automatic checks found nothing unusual.</p>
          }
          <div class="review-files">
            <ul class="files-list" aria-label="Files">
              @for (f of r.review.files; track f.path) {
                <li>
                  <button type="button" class="files-entry" [class.current]="file()?.path === f.path" (click)="openFile(f.path)">
                    <span class="grow mono">{{ f.path }}</span>
                    @if (f.script) {
                      <span class="chip chip-wait">script</span>
                    }
                    <span class="muted small">{{ bytes(f.size) }}</span>
                  </button>
                </li>
              }
            </ul>
            <div class="review-viewer">
              @if (file(); as f) {
                @if (f.line) {
                  <p class="small muted">Line {{ f.line }}</p>
                }
                <div deskFileViewer [path]="f.path" [data]="f.data"></div>
              }
            </div>
          </div>
          @if (installed(); as done) {
            <div class="review-done">
              <strong>{{ done.name }} is installed{{ where(done) }}.</strong>
              @if (installedHere(); as here) {
                <div deskRuntimeLine [entry]="r.entry" [install]="here" (retried)="changed.emit()"></div>
              }
            </div>
          } @else {
            <div deskField id="review-scope" label="Install for">
              <select id="review-scope" class="select" (change)="pickScope($event)">
                <option value="global" [selected]="scope() === 'global'">Every project (global)</option>
                @for (p of projects(); track p.id) {
                  <option [value]="p.id" [selected]="scope() === p.id">{{ p.name }} only</option>
                }
              </select>
            </div>
            @if (action().kind === 'installed') {
              <p class="small muted">This version is already installed there.</p>
            }
            @if (action().kind === 'taken') {
              <p class="field-error">A skill named {{ r.entry.id }} already exists there and didn't come from the catalog. Rename or delete it first.</p>
            }
            @if (action().kind === 'modified') {
              <label class="check"><input type="checkbox" [checked]="replace()" (change)="replace.set(checked($event))" /> {{ r.entry.id }} was edited after it was installed. Replace those changes (they stay in its history).</label>
            }
          }
        </div>
      } @else {
        <p class="muted">Fetching the pinned files and checking them…</p>
      }
      <div class="sheet-footer">
        @if (installed(); as done) {
          <button deskButton (click)="close.emit()">Close</button>
          <button deskButton variant="primary" (click)="openSkill(done)">Open skill</button>
        } @else {
          <button deskButton [disabled]="pending()" (click)="dismiss()">Cancel</button>
          <button deskButton variant="primary" [pending]="pending()" [disabled]="installOff()" (click)="install()">{{ installLabel() }}</button>
        }
      </div>
    </div>
  `,
})
export class ReviewSheet {
  readonly id = input.required<string>();
  /** The catalog entry with its installs, as last listed (absent while the catalog loads). */
  readonly item = input<CatalogItem>();
  readonly projects = input.required<Array<{ id: string; name: string }>>();
  /** Something was installed or retried: the screen lists skills and the catalog again (React's `onChanged`). */
  readonly changed = output<void>();
  readonly close = output<void>();

  private readonly bridge = inject(DeskBridge);
  private readonly routes = inject(RouteService);
  private readonly toasts = inject(ToastService);
  /** The skills screen, told directly about an install that lands after this sheet closed. */
  private readonly screen = inject(SkillsRefresh, { optional: true });
  /** Set once the sheet is gone (browser Back while the install ran): `changed` then reaches no one. */
  private closed = false;
  protected readonly warning = WARNING;
  protected readonly bytes = bytes;
  protected readonly runtimeWords = runtimeWords;

  protected readonly review = signal<CatalogReview | null>(null);
  protected readonly error = signal<string | null>(null);
  protected readonly file = signal<OpenFile | null>(null);
  protected readonly scope = signal('global');
  protected readonly replace = signal(false);
  protected readonly showLicense = signal(false);
  protected readonly pending = signal(false);
  protected readonly installed = signal<SkillRef | null>(null);

  private readonly projectId = computed(() => (this.scope() === 'global' ? undefined : this.scope()));
  private readonly current = computed(() => {
    const projectId = this.projectId();
    return this.item()?.installs.find((i) => (projectId ? i.scope === 'project' && i.project_id === projectId : i.scope === 'global'));
  });
  protected readonly action = computed(() => actionFor(this.current()));
  protected readonly installedHere = computed(() => {
    const done = this.installed();
    if (!done) return undefined;
    const key = skillKey(done);
    return this.item()?.installs.find((i) => skillKey(installRef(this.id(), i)) === key);
  });
  private readonly entry = computed((): CatalogEntry | undefined => this.review()?.entry ?? this.item());
  protected readonly packages = computed(() => {
    const e = this.entry();
    return e ? runtimePackages(e) : [];
  });
  protected readonly ready = computed(() => {
    const review = this.review();
    const entry = this.entry();
    return review && entry ? { review, entry } : null;
  });
  protected readonly sourceLink = computed(() => {
    const r = this.ready();
    const source = r?.entry.source;
    if (!r || !r.review.source_url || source?.type !== 'github') return null;
    return { url: r.review.source_url, label: sourceLabel(r.entry), sha: source.sha.slice(0, 7) };
  });
  protected readonly title = computed(() => {
    const e = this.entry();
    return e ? `${this.action().kind === 'update' ? 'Update' : 'Install'} ${e.title}` : 'Review a skill';
  });
  protected readonly installOff = computed(() => {
    const kind = this.action().kind;
    return !this.review() || kind === 'installed' || kind === 'taken' || (kind === 'modified' && !this.replace());
  });
  protected readonly installLabel = computed(() => {
    const kind = this.action().kind;
    return kind === 'installed' ? 'Installed' : kind === 'update' ? 'Update' : kind === 'modified' ? 'Replace and install' : 'Install';
  });

  constructor() {
    inject(DestroyRef).onDestroy(() => (this.closed = true));
    // React's effect on [id]: prepare the entry, open its SKILL.md, and drop a late answer for another entry.
    // The call runs untracked, so a signal it reads (the bridge's signedOut on a sign-in retry) never prepares again.
    effect((onCleanup) => {
      const id = this.id();
      let live = true;
      onCleanup(() => (live = false));
      this.review.set(null);
      this.error.set(null);
      untracked(() => this.bridge.call('catalog.prepare', { id }))
        .then((r) => {
          if (!live) return;
          this.review.set(r);
          this.file.set({ path: 'SKILL.md', data: encoder.encode(r.skill_md) });
        })
        .catch((err: unknown) => {
          if (live) this.error.set(describeError(err).message);
        });
    });
  }

  /** Cancel, Escape and the backdrop do nothing while the install runs, so its `changed` still reaches the screen. */
  protected dismiss(): void {
    if (!this.pending()) this.close.emit();
  }

  protected checked(e: Event): boolean {
    return (e.target as HTMLInputElement).checked;
  }

  protected pickScope(e: Event): void {
    this.scope.set((e.target as HTMLSelectElement).value);
    this.replace.set(false);
  }

  protected where(done: SkillRef): string {
    return done.scope === 'project' ? ` in ${this.projects().find((p) => p.id === done.projectId)?.name ?? 'the project'}` : ' for every project';
  }

  protected async openFile(path: string, line?: number): Promise<void> {
    const review = this.review();
    if (path === 'SKILL.md' && review) {
      this.file.set({ path, data: encoder.encode(review.skill_md), ...(line ? { line } : {}) });
      return;
    }
    try {
      const data = await this.bridge.call('catalog.file', { id: this.id(), path });
      this.file.set({ path, data, ...(line ? { line } : {}) });
    } catch (err) {
      this.toasts.error(err);
    }
  }

  protected async install(): Promise<void> {
    const projectId = this.projectId();
    const kind = this.action().kind;
    this.pending.set(true);
    try {
      const r = await this.bridge.call('catalog.install', { id: this.id(), ...(projectId ? { projectId } : {}), ...(kind === 'modified' ? { replaceModified: true } : {}) });
      const ref: SkillRef = r.skill.scope === 'global' ? { scope: 'global', name: r.skill.name } : { scope: 'project', projectId: r.skill.project_id!, name: r.skill.name };
      this.installed.set(ref);
      this.toasts.toast({ tone: 'info', message: `${kind === 'update' ? 'Updated' : 'Installed'} ${r.skill.name}.` });
      // `changed` while the sheet is open, else the screen itself (never both): browser Back closes it mid-install.
      if (this.closed) this.screen?.changed();
      else this.changed.emit();
    } catch (err) {
      this.toasts.error(err);
    } finally {
      this.pending.set(false);
    }
  }

  protected openSkill(done: SkillRef): void {
    this.routes.navigate({ name: 'skills', skill: skillKey(done) });
  }
}
