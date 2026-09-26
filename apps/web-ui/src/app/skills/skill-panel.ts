import { ChangeDetectionStrategy, Component, DestroyRef, ViewEncapsulation, computed, effect, inject, input, linkedSignal, output, signal, untracked } from '@angular/core';
import type { SkillDetail, SkillHistoryEntry, SkillNode } from '@desk/client';
import type { CatalogInstall, CatalogItem } from '@desk/protocol';
import { bytes, diffFiles, diffLines, href, since, skillKey, withContext, type FileChange, type SkillRef } from '@desk/ui-core';
import { Button } from '../components/button';
import { ConfirmDialog } from '../components/confirm-dialog';
import { EmptyState } from '../components/empty-state';
import { FileViewer } from '../components/file-viewer';
import { SafeMarkdown } from '../components/safe-markdown';
import { describeError, ToastService } from '../components/toast';
import { DeskBridge } from '../core/desk-bridge';
import { NowService } from '../core/now.service';
import { RouteService } from '../core/route.service';
import { runtimeWords, sourceLabel } from './catalog/data';
import { builtinKey } from './builtins/data';
import { RuntimeLine } from './catalog/runtime-line';
import { scopeArg, whoLabel } from './data';
import { SkillsRefresh } from './skills-refresh';

type Tab = 'overview' | 'instructions' | 'files' | 'history';
const TABS: Tab[] = ['overview', 'instructions', 'files', 'history'];
type Confirming = { kind: 'delete' } | { kind: 'restore'; version: number };
type OpenFile = { path: string; data: Uint8Array };
type Use = SkillNode['usedBy'][number];

/** Each file change's chip tone; the template adds `chip`. */
const CHIP: Record<FileChange['change'], string> = { added: 'chip-run', removed: 'chip-fail', changed: 'chip-idle', same: 'chip-idle' };

/** Two versions side by side: the instruction lines that changed, and the files added, removed or resized (SkillPanel.tsx `Compare`). */
@Component({
  selector: 'div[deskCompare]',
  changeDetection: ChangeDetectionStrategy.OnPush,
  encapsulation: ViewEncapsulation.None,
  host: { class: 'skill-compare' },
  template: `
    <div class="skill-compare-bar">
      <label>Compare&ngsp;<select class="select" aria-label="From version" (change)="from.set(num($event))">@for (v of versions(); track v) {<option [value]="v" [selected]="v === from()">v{{ v }}</option>}</select></label>
      <label>with&ngsp;<select class="select" aria-label="To version" (change)="to.set(num($event))">@for (v of versions(); track v) {<option [value]="v" [selected]="v === to()">v{{ v }}</option>}</select></label>
    </div>
    @if (error(); as err) {
      <p class="field-error">{{ err }}</p>
    }
    @if (pair()) {
      @if (patch().length) {
        <pre class="diff-patch" [attr.aria-label]="'Changes from v' + from() + ' to v' + to()">@for (l of patch(); track $index) {<span [attr.class]="l.cls">{{ l.text }}</span>}</pre>
      } @else {
        <p class="muted small">The instructions are the same.</p>
      }
      @if (files().length) {
        <ul class="skill-file-changes">
          @for (f of files(); track f.path) {
            <li><span class="chip" [class]="chip[f.change]">{{ f.change }}</span>&ngsp;<span class="mono">{{ f.path }}</span>@if (f.change === 'changed') {<span class="muted small"> {{ bytes(f.before ?? 0) }} → {{ bytes(f.after ?? 0) }}</span>}</li>
          }
        </ul>
      }
    } @else if (!error()) {
      <p class="muted small">Loading…</p>
    }
  `,
})
export class Compare {
  readonly skill = input.required<SkillRef>();
  readonly history = input.required<SkillHistoryEntry[]>();

  private readonly bridge = inject(DeskBridge);
  protected readonly chip = CHIP;
  protected readonly bytes = bytes;
  protected readonly versions = computed(() => this.history().map((h) => h.version));
  /** The versions first compared: the one before the current, and the current (React's useState seeds). */
  private readonly firstFrom = computed(() => this.versions().at(-2) ?? this.versions()[0] ?? 1);
  private readonly firstTo = computed(() => this.versions().at(-1) ?? 1);
  /** The two versions compared: the first ones, then whatever the user picks, kept when the history is fetched again (React's useState). */
  protected readonly from = linkedSignal<number, number>({ source: this.firstFrom, computation: (v, prev) => prev?.value ?? v });
  protected readonly to = linkedSignal<number, number>({ source: this.firstTo, computation: (v, prev) => prev?.value ?? v });
  protected readonly pair = signal<[SkillDetail, SkillDetail] | null>(null);
  protected readonly error = signal<string | null>(null);
  private readonly key = computed(() => skillKey(this.skill()));

  protected readonly patch = computed(() => {
    const p = this.pair();
    if (!p) return [];
    return withContext(diffLines(`${p[0].description}\n\n${p[0].instructions}`, `${p[1].description}\n\n${p[1].instructions}`)).map((l) =>
      l === null
        ? { cls: 'diff-hunk', text: '…\n' }
        : { cls: l.kind === 'add' ? 'diff-line-add' : l.kind === 'del' ? 'diff-line-del' : null, text: `${l.kind === 'add' ? '+ ' : l.kind === 'del' ? '- ' : '  '}${l.text}\n` },
    );
  });
  protected readonly files = computed(() => {
    const p = this.pair();
    return p ? diffFiles(p[0].files, p[1].files).filter((f) => f.change !== 'same') : [];
  });

  constructor() {
    // React's effect on [scope, projectId, name, from, to]; a late answer for an older pair is dropped.
    effect((onCleanup) => {
      this.key();
      const from = this.from();
      const to = this.to();
      const skill = untracked(this.skill);
      let live = true;
      onCleanup(() => (live = false));
      this.pair.set(null);
      // The calls run untracked, so a signal the bridge reads (signedOut on a sign-in retry) never fetches the pair again.
      untracked(() => Promise.all([this.bridge.call('skills.version', { ...scopeArg(skill), name: skill.name, version: from }), this.bridge.call('skills.version', { ...scopeArg(skill), name: skill.name, version: to })]))
        .then(([a, b]) => {
          if (!live) return;
          this.pair.set([a, b]);
          this.error.set(null);
        })
        .catch((err: unknown) => {
          if (live) this.error.set(describeError(err).message);
        });
    });
  }

  protected num(e: Event): number {
    return Number((e.target as HTMLSelectElement).value);
  }
}

/** One skill in full: overview, instructions, files and history (compare any two versions, restore), with edit, delete and Desk (SkillPanel.tsx). */
@Component({
  selector: 'article[deskSkillPanel]',
  imports: [Button, Compare, ConfirmDialog, EmptyState, FileViewer, RuntimeLine, SafeMarkdown],
  changeDetection: ChangeDetectionStrategy.OnPush,
  encapsulation: ViewEncapsulation.None,
  host: { class: 'card skill-panel', '[attr.aria-label]': "'Skill ' + skill().name" },
  template: `
    <div class="skill-panel-head">
      <h2 class="mono">{{ skill().name }}</h2>
      <span class="skill-scope" [class]="skill().scope">{{ scopeLabel() }}</span>
      <button type="button" class="icon-btn" aria-label="Close" (click)="close.emit()">✕</button>
    </div>
    @if (error(); as err) {
      <div deskEmptyState title="Couldn't load this skill" [body]="err"></div>
    } @else if (detail(); as d) {
      <p class="skill-desc">{{ d.description }}</p>
      @if (catalog(); as c) {
        <div class="skill-catalog">
          <div class="skill-catalog-row">
            <span class="chip chip-done">From catalog</span>
            <span class="small muted grow">{{ catalogLine() }}</span>
            @if (c.install.state === 'update_available') {
              <button deskButton size="sm" variant="primary" (click)="openUpdate(c.item.id)">Update available</button>
            } @else if (c.install.state === 'modified') {
              <span class="chip chip-wait" title="Edited since it was installed from the catalog">edited</span>
            }
          </div>
          <div deskRuntimeLine [entry]="c.item" [install]="c.install" (retried)="changed.emit()"></div>
        </div>
      }
      @if (fromBuiltin(); as b) {
        <p class="shadow-note">Customised from the built-in skill <a [href]="builtinHref(b)">{{ b }}</a>. Agents use this copy instead; delete it to bring the built-in back.</p>
      }
      @if (d.error) {
        <p class="field-error" role="alert">SKILL.md has a problem, so agents can't use this skill until it's fixed: {{ d.error }}</p>
      }
      <div class="tabs" role="tablist" aria-label="Skill">
        @for (t of tabs; track t) {
          <button type="button" role="tab" [attr.aria-selected]="tab() === t" (click)="tab.set(t)">{{ tabLabel(t, d) }}</button>
        }
      </div>
      <div class="skill-tab" role="tabpanel">
        @switch (tab()) {
          @case ('overview') {
            @if (shadows()) {
              <p class="shadow-note">{{ scopeLabel() }} has its own {{ skill().name }}, so agents there use this one instead of the global skill.</p>
            }
            @if (shadowedCount()) {
              <p class="shadow-note">{{ shadowedNames() }} {{ shadowedCount() === 1 ? 'has' : 'have' }} its own {{ skill().name }}, so agents there use that one. Everywhere else they use this.</p>
            }
            @if (current(); as h) {
              <div class="skill-change">
                <strong class="small">{{ who(h.origin) }} · v{{ h.version }}{{ h.ts ? ' · ' + since(h.ts, now()) : '' }}</strong>
                <span class="small">{{ h.change_note || 'No change note.' }}</span>
              </div>
            }
            <div class="skill-versions" role="group" aria-label="Versions">
              @for (h of newestFirst(); track h.version) {
                <button type="button" [attr.class]="h.current ? 'on' : null" [attr.aria-pressed]="h.current" (click)="tab.set('history')">v{{ h.version }}</button>
              }
            </div>
            <div>
              <span class="label">Used by</span>
              @if (usedBy().length) {
                <ul class="skill-used">
                  @for (u of usedBy(); track u.threadId) {
                    <li><span class="status-dot" [class]="'status-dot-' + u.status" aria-hidden="true"></span><a [href]="threadHref(u)">{{ u.title ?? 'Thread' }}</a><span class="muted small"> · {{ u.status }} · {{ projectNames().get(u.projectId) ?? '' }}</span></li>
                  }
                </ul>
              } @else {
                <p class="muted small">Not in use right now.</p>
              }
            </div>
          }
          @case ('instructions') {
            <div deskSafeMarkdown [text]="d.instructions"></div>
          }
          @case ('files') {
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
          @default {
            <ol class="skill-history" reversed>
              @for (h of newestFirst(); track h.version) {
                <li>
                  <span class="mono">v{{ h.version }}</span>
                  <span class="grow">
                    <span class="small"><strong>{{ who(h.origin) }}</strong>{{ h.ts ? ' · ' + since(h.ts, now()) : '' }}</span>
                    <span class="small muted">{{ h.change_note || h.description }}</span>
                  </span>
                  @if (h.current) {
                    <span class="chip chip-done">current</span>
                  } @else {
                    <button deskButton size="sm" [pending]="busyHere('restore-' + h.version)" (click)="askRestore(h.version)">Restore</button>
                  }
                </li>
              }
            </ol>
            @if (history().length > 1) {
              <div deskCompare [skill]="skill()" [history]="history()"></div>
            }
          }
        }
      </div>
      <div class="skill-actions">
        <button deskButton variant="primary" (click)="edit.emit(d)">Edit</button>
        <button deskButton (click)="askDesk.emit()">Refine with Desk</button>
        <span class="grow"></span>
        <button deskButton variant="ghost" [pending]="busyHere('delete')" (click)="askDelete()">Delete</button>
      </div>
    } @else {
      <p class="muted">Loading…</p>
    }
    @if (restoring(); as v) {
      <div deskConfirmDialog [title]="'Restore v' + v + '?'" confirmLabel="Restore" (cancel)="confirming.set(null)" (confirm)="restore(v)">It comes back as the newest version. Nothing in the history is lost.</div>
    }
    @if (deleting()) {
      <div deskConfirmDialog [title]="'Delete ' + skill().name + '?'" confirmLabel="Delete" danger (cancel)="confirming.set(null)" (confirm)="remove()">Agents stop using it. Its past versions stay in the skill's history on disk.</div>
    }
  `,
})
export class SkillPanel {
  readonly skill = input.required<SkillRef>();
  readonly node = input<SkillNode>();
  /** Set when the skill was installed from the catalog. */
  readonly catalog = input<{ item: CatalogItem; install: CatalogInstall }>();
  readonly projectNames = input.required<Map<string, string>>();
  readonly threadTitles = input.required<Map<string, string>>();
  /** Bumped by the screen whenever something changed, so the panel fetches the skill again. */
  readonly version = input.required<number>();
  readonly edit = output<SkillDetail>();
  readonly askDesk = output<void>();
  readonly changed = output<void>();
  readonly close = output<void>();

  private readonly bridge = inject(DeskBridge);
  private readonly routes = inject(RouteService);
  private readonly toasts = inject(ToastService);
  /** The skills screen, told directly about an action that lands after this panel closed. */
  private readonly screen = inject(SkillsRefresh, { optional: true });
  protected readonly now = inject(NowService).now;
  protected readonly tabs = TABS;
  protected readonly bytes = bytes;
  protected readonly since = since;

  private readonly key = computed(() => skillKey(this.skill()));
  /** Back to Overview, with no file open, whenever another skill opens (React's second effect). */
  protected readonly tab = linkedSignal<string, Tab>({ source: this.key, computation: () => 'overview' });
  protected readonly file = linkedSignal<string, OpenFile | null>({ source: this.key, computation: () => null });
  protected readonly detail = signal<SkillDetail | null>(null);
  protected readonly history = signal<SkillHistoryEntry[]>([]);
  protected readonly error = signal<string | null>(null);
  /** The open Delete or Restore confirm; another skill opening closes it, so it never acts on that one. */
  protected readonly confirming = linkedSignal<string, Confirming | null>({ source: this.key, computation: () => null });
  /** Every action running, as `<skill key>|<what>`: each skill's buttons show their own, however the user moves between skills. */
  private readonly busy = signal<ReadonlySet<string>>(new Set());
  private readonly reload = signal(0);

  protected readonly current = computed(() => this.history().find((h) => h.current));
  /** The built-in skill this one was duplicated from, if any (a `builtin:<name>@<digest>` origin). */
  protected readonly fromBuiltin = computed(() => this.history().find((h) => h.origin?.startsWith('builtin:'))?.origin?.slice(8).split('@')[0]);
  protected readonly newestFirst = computed(() => this.history().slice().reverse());
  protected readonly scopeLabel = computed(() => {
    const s = this.skill();
    return s.scope === 'global' ? 'Global' : (this.projectNames().get(s.projectId ?? '') ?? 'Project');
  });
  protected readonly shadows = computed(() => this.node()?.shadows ?? false);
  protected readonly shadowedCount = computed(() => this.node()?.shadowedIn.length ?? 0);
  protected readonly shadowedNames = computed(() => (this.node()?.shadowedIn ?? []).map((id) => this.projectNames().get(id) ?? id).join(', '));
  protected readonly usedBy = computed(() => this.node()?.usedBy ?? []);
  protected readonly catalogLine = computed(() => {
    const c = this.catalog();
    if (!c) return '';
    return `${sourceLabel(c.item)} · ${c.item.license}${c.install.runtime === 'none' ? ` · ${runtimeWords(c.item)}` : ''}`;
  });
  protected readonly restoring = computed(() => {
    const c = this.confirming();
    return c?.kind === 'restore' ? c.version : null;
  });
  protected readonly deleting = computed(() => this.confirming()?.kind === 'delete');
  /** Set once the panel is gone (the route left the skill while an action ran): its outputs then reach no one. */
  private closed = false;

  constructor() {
    inject(DestroyRef).onDestroy(() => (this.closed = true));
    // React's effect on [scope, projectId, name, version, reload]: the skill and its history; a late answer is dropped.
    effect((onCleanup) => {
      this.key();
      this.version();
      this.reload();
      const skill = untracked(this.skill);
      let live = true;
      onCleanup(() => (live = false));
      this.error.set(null);
      // The calls run untracked, so a signal the bridge reads (signedOut on a sign-in retry) never fetches the skill again.
      untracked(() => Promise.all([this.bridge.call('skills.get', { ...scopeArg(skill), name: skill.name }), this.bridge.call('skills.history', { ...scopeArg(skill), name: skill.name })]))
        .then(([d, h]) => {
          if (!live) return;
          this.detail.set(d);
          this.history.set(h);
        })
        .catch((err: unknown) => {
          if (live) this.error.set(describeError(err).message);
        });
    });
  }

  /** Whether `what` (`delete`, `restore-<version>`) is running on the open skill. */
  protected busyHere(what: string): boolean {
    return this.busy().has(`${this.key()}|${what}`);
  }

  protected tabLabel(t: Tab, d: SkillDetail): string {
    if (t === 'overview') return 'Overview';
    if (t === 'instructions') return 'Instructions';
    return t === 'files' ? `Files · ${d.files.length}` : `History · ${this.history().length}`;
  }

  protected who(origin: string | null): string {
    return whoLabel(origin, this.threadTitles());
  }

  protected builtinHref(name: string): string {
    return href({ name: 'skills', skill: builtinKey(name) });
  }

  protected threadHref(u: Use): string {
    return href({ name: 'project', id: u.projectId, tab: 'threads', threadId: u.threadId });
  }

  protected openUpdate(id: string): void {
    this.routes.navigate({ name: 'catalog', review: id });
  }

  protected async openFile(path: string): Promise<void> {
    const skill = this.skill();
    const key = this.key();
    try {
      const data = await this.bridge.call('skills.file', { ...scopeArg(skill), name: skill.name, path });
      // Another skill opened meanwhile: this file is not one of its files.
      if (this.key() === key) this.file.set({ path, data });
    } catch (err) {
      if (this.key() === key) this.toasts.error(err);
    }
  }

  protected askRestore(version: number): void {
    this.confirming.set({ kind: 'restore', version });
  }

  protected askDelete(): void {
    this.confirming.set({ kind: 'delete' });
  }

  protected restore(version: number): void {
    const skill = this.skill();
    void this.act(`restore-${version}`, () => this.bridge.call('skills.restore', { ...scopeArg(skill), name: skill.name, version }), `Restored v${version} as a new version.`);
  }

  protected remove(): void {
    const skill = this.skill();
    const key = this.key();
    void this.act('delete', () => this.bridge.call('skills.remove', { ...scopeArg(skill), name: skill.name }), `Deleted ${skill.name}.`).then((ok) => {
      // Closing now would close the skill the user opened since, or leave wherever the user went once the panel closed.
      if (ok && !this.closed && this.key() === key) this.close.emit();
    });
  }

  /**
   * Runs one action on the open skill; its toast and `changed` still come when another skill opened meanwhile, the reload
   * does not. Once the panel closed, the screen hears of it directly (`changed` would reach no one).
   */
  private async act(what: string, fn: () => Promise<unknown>, done: string): Promise<boolean> {
    const key = this.key();
    const mark = `${key}|${what}`;
    this.confirming.set(null);
    this.busy.update((b) => new Set(b).add(mark));
    try {
      await fn();
      this.toasts.toast({ tone: 'info', message: done });
      if (this.closed) this.screen?.changed();
      else this.changed.emit();
      if (this.key() === key) this.reload.update((r) => r + 1);
      return true;
    } catch (err) {
      this.toasts.error(err);
      return false;
    } finally {
      // Clears only its own mark, so every other action, on this skill or another, keeps its own.
      this.busy.update((b) => {
        const next = new Set(b);
        next.delete(mark);
        return next;
      });
    }
  }
}
