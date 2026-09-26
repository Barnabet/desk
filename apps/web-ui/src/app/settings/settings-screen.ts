import { ChangeDetectionStrategy, Component, ViewEncapsulation, computed, inject, input, linkedSignal, signal } from '@angular/core';
import type { PolicyRule, ProjectSettings } from '@desk/protocol';
import { Button } from '../components/button';
import { ConfirmDialog } from '../components/confirm-dialog';
import { EmptyState } from '../components/empty-state';
import { Field } from '../components/field';
import { ToastService } from '../components/toast';
import { DeskBridge } from '../core/desk-bridge';
import { RouteService } from '../core/route.service';
import { injectSession } from '../core/session.service';
import { injectModels } from './models';
import { PolicyEditor, sameRules } from './policy-editor';
import { SettingsFields, workingStyleOf, type WorkingStyle } from './settings-fields';

type About = { name: string; goal: string; instructions: string };

const sameAbout = (a: About | null, b: About | null): boolean =>
  a === b || (!!a && !!b && a.name === b.name && a.goal === b.goal && a.instructions === b.instructions);
/** React's `settingsKey`: settings compared as JSON. */
const sameSettings = (a: ProjectSettings | null, b: ProjectSettings | null): boolean => JSON.stringify(a) === JSON.stringify(b);
const val = (e: Event): string => (e.target as HTMLInputElement | HTMLTextAreaElement).value;

/** A project's settings: what it is, where its sources are, how Desk works, the policy, and archiving (SettingsScreen.tsx). */
@Component({
  selector: 'div[deskSettingsScreen]',
  imports: [Button, ConfirmDialog, EmptyState, Field, PolicyEditor, SettingsFields],
  changeDetection: ChangeDetectionStrategy.OnPush,
  encapsulation: ViewEncapsulation.None,
  host: { class: 'page', '[class]': 'hostClass()' },
  template: `
    @switch (view()) {
      @case ('loading') {Loading…}
      @case ('error') {
        <div deskEmptyState title="Couldn't load this project" [body]="s().error"></div>
      }
      @default {
        @if (form(); as f) {
          <h1 class="title">Settings</h1>

          <section class="card settings-section" aria-labelledby="set-about">
            <h2 id="set-about">About this project</h2>
            <div deskField id="set-name" label="Name">
              <input id="set-name" class="input" [value]="f.about.name" (input)="patchAbout({ name: val($event) })" />
            </div>
            <div deskField id="set-goal" label="Goal">
              <textarea id="set-goal" class="textarea" [value]="f.about.goal" (input)="patchAbout({ goal: val($event) })"></textarea>
            </div>
            <div deskField id="set-instructions" label="Standing instructions" hint="Desk and every thread read these before they start.">
              <textarea id="set-instructions" class="textarea" rows="4" [value]="f.about.instructions" (input)="patchAbout({ instructions: val($event) })"></textarea>
            </div>
            <div class="actions">
              <button deskButton variant="primary" [pending]="busy().has('about')" [disabled]="!aboutDirty() || !f.about.name.trim()" (click)="saveAbout()">Save</button>
            </div>
          </section>

          <section class="card settings-section" aria-labelledby="set-sources">
            <h2 id="set-sources">Sources</h2>
            <p class="field-hint">Folders Desk and its threads work with. A git repository gets its own branch per thread; Desk never merges. With write access, agents can also change files here and run the project's own tools and servers (sandboxed, without your secrets).</p>
            @if (f.sources.length) {
              <ul class="sources">
                @for (src of f.sources; track src.id) {
                  <li>
                    <span class="chip" [class]="src.kind === 'git' ? 'chip-run' : 'chip-idle'">{{ src.kind }}</span>
                    <span class="grow"><strong>{{ src.label }}</strong>&ngsp;<span class="mono small muted">{{ src.path }}</span></span>
                    <label class="source-write"><input type="checkbox" [checked]="src.agent_write" [disabled]="busy().has('w-' + src.id)" (change)="setWrite(src.id, $event)" />Agents can write here</label>
                    <button deskButton size="sm" variant="ghost" [attr.aria-label]="'Remove ' + src.label" [pending]="busy().has('rm-' + src.id)" (click)="removeSource(src.id)">Remove</button>
                  </li>
                }
              </ul>
            } @else {
              <p class="muted">No sources yet.</p>
            }
            <div>
              <button deskButton size="sm" [pending]="busy().has('source')" (click)="addSource()">Add folder…</button>
            </div>
          </section>

          <section class="card settings-section" aria-labelledby="set-style">
            <h2 id="set-style">How Desk works</h2>
            <div deskSettingsFields [value]="f.style" [models]="models()" (changed)="patchStyle($event)"></div>
            <div class="actions">
              <button deskButton variant="primary" [pending]="busy().has('style')" [disabled]="!styleDirty()" (click)="saveStyle()">Save</button>
              @if (styleDirty()) {
                <button deskButton variant="ghost" (click)="discardStyle()">Discard changes</button>
              }
            </div>
          </section>

          <section class="card settings-section" aria-labelledby="set-policy">
            <h2 id="set-policy">Policy</h2>
            <div deskPolicyEditor [rules]="f.policy" (changed)="policy.set($event)"></div>
            <div class="actions">
              <button deskButton variant="primary" [pending]="busy().has('policy')" [disabled]="!policyDirty() || policyIncomplete()" (click)="savePolicy()">Save policy</button>
              @if (policyDirty()) {
                <button deskButton variant="ghost" (click)="discardPolicy()">Discard changes</button>
              }
            </div>
          </section>

          <section class="card settings-section danger" aria-labelledby="set-archive">
            <h2 id="set-archive">Archive</h2>
            <p class="small">Archiving stops the project's threads and hides it from the map. Its library, memory and branches are kept.</p>
            <div>
              <button deskButton variant="danger" [pending]="busy().has('archive')" (click)="confirmArchive.set(true)">Archive project…</button>
            </div>
          </section>
          @if (confirmArchive()) {
            <div deskConfirmDialog [title]="'Archive ' + f.project.name + '?'" confirmLabel="Archive" danger (cancel)="confirmArchive.set(false)" (confirm)="archive()">Running threads are stopped. Nothing is deleted.</div>
          }
        }
      }
    }
  `,
})
export class SettingsScreen {
  readonly projectId = input.required<string>();

  private readonly bridge = inject(DeskBridge);
  private readonly routes = inject(RouteService);
  private readonly toasts = inject(ToastService);
  protected readonly s = injectSession(this.projectId);
  protected readonly models = injectModels();
  private readonly project = computed(() => this.s().project?.project ?? null);

  /** What is being written now (React's `useSaver`): sections, `source`, `w-<id>`, `rm-<id>`, `archive`. One key per
   *  control, so overlapping writes never clear or re-enable each other's. */
  protected readonly busy = signal<ReadonlySet<string>>(new Set());
  protected readonly confirmArchive = signal(false);

  // Drafts start from the live project and reset when it changes underneath (after a save, or another client). Each
  // linkedSignal follows a computed that compares by value (React's effect dependencies), never the project object, which
  // every event and reload rebuilds: a push with the same values keeps what is being typed (the port's Signals rule).
  private readonly liveAbout = computed<About | null>(
    () => {
      const p = this.project();
      return p ? { name: p.name, goal: p.goal, instructions: p.instructions } : null;
    },
    { equal: sameAbout },
  );
  private readonly liveSettings = computed<ProjectSettings | null>(() => this.project()?.settings ?? null, { equal: sameSettings });
  protected readonly about = linkedSignal<About | null>(() => this.liveAbout());
  protected readonly style = linkedSignal<WorkingStyle | null>(() => {
    const settings = this.liveSettings();
    return settings ? workingStyleOf(settings) : null;
  });
  protected readonly policy = linkedSignal<PolicyRule[] | null>(() => this.liveSettings()?.policy ?? null);

  /** React's early returns: "Loading…" while it loads, the failure (a project that never loaded has no drafts), then
   *  "Loading…" until the drafts exist, then the screen. */
  protected readonly view = computed<'loading' | 'error' | 'ready'>(() => {
    const s = this.s();
    if (s.status === 'loading') return 'loading';
    if (s.status !== 'ready' || !s.project) return 'error';
    return this.about() && this.style() && this.policy() ? 'ready' : 'loading';
  });
  /** The class beside the fixed `page`. */
  protected readonly hostClass = computed(() => ({ loading: 'muted', error: '', ready: 'settings' })[this.view()]);
  protected readonly form = computed(() => {
    const s = this.s();
    const about = this.about();
    const style = this.style();
    const policy = this.policy();
    return this.view() === 'ready' && s.project && about && style && policy ? { project: s.project.project, sources: s.project.sources, about, style, policy } : null;
  });

  protected readonly aboutDirty = computed(() => {
    const a = this.about();
    const p = this.project();
    return !!a && !!p && (a.name !== p.name || a.goal !== p.goal || a.instructions !== p.instructions);
  });
  protected readonly styleDirty = computed(() => {
    const style = this.style();
    const p = this.project();
    return !!style && !!p && JSON.stringify(style) !== JSON.stringify(workingStyleOf(p.settings));
  });
  protected readonly policyDirty = computed(() => {
    const rules = this.policy();
    const p = this.project();
    return !!rules && !!p && !sameRules(rules, p.settings.policy);
  });
  protected readonly policyIncomplete = computed(() => (this.policy() ?? []).some((r) => !r.tool.trim()));

  protected readonly val = val;

  protected patchAbout(patch: Partial<About>): void {
    this.about.update((a) => (a ? { ...a, ...patch } : a));
  }

  protected patchStyle(patch: Partial<WorkingStyle>): void {
    this.style.update((s) => (s ? { ...s, ...patch } : s));
  }

  protected saveAbout(): void {
    const about = this.about();
    if (!about) return;
    void this.run('about', () => this.bridge.call('projects.update', { id: this.projectId(), patch: { name: about.name.trim(), goal: about.goal, instructions: about.instructions } }), 'Saved.');
  }

  protected saveStyle(): void {
    const style = this.style();
    if (!style) return;
    void this.run('style', () => this.bridge.call('projects.update', { id: this.projectId(), patch: { settings: style } }), 'Saved.');
  }

  protected discardStyle(): void {
    const p = this.project();
    if (p) this.style.set(workingStyleOf(p.settings));
  }

  protected savePolicy(): void {
    const policy = this.policy();
    if (!policy) return;
    void this.run('policy', () => this.bridge.call('projects.update', { id: this.projectId(), patch: { settings: { policy } } }), 'Policy saved.');
  }

  protected discardPolicy(): void {
    const p = this.project();
    if (p) this.policy.set(p.settings.policy);
  }

  /** On the web, app.pickFolder is the folder browser (spec §3); deskd checks the folder it gets (§4.7). */
  protected async addSource(): Promise<void> {
    const path = await this.bridge.call('app.pickFolder', { purpose: 'source' }).catch((err: unknown) => {
      this.toasts.error(err);
      return null;
    });
    if (path) await this.run('source', () => this.bridge.call('projects.addSource', { id: this.projectId(), source: { path } }));
  }

  /** Controlled, as React's checkbox: the box shows deskd's value until the source.updated event lands. */
  protected setWrite(sourceId: string, e: Event): void {
    const box = e.target as HTMLInputElement;
    const agentWrite = box.checked;
    box.checked = !agentWrite;
    void this.run(`w-${sourceId}`, () => this.bridge.call('projects.setSourceWrite', { id: this.projectId(), sourceId, agentWrite }));
  }

  protected removeSource(sourceId: string): void {
    void this.run(`rm-${sourceId}`, () => this.bridge.call('projects.removeSource', { id: this.projectId(), sourceId }));
  }

  protected archive(): void {
    this.confirmArchive.set(false);
    void this.run('archive', () => this.bridge.call('projects.archive', { id: this.projectId() })).then((ok) => {
      if (ok) this.routes.navigate({ name: 'map' });
    });
  }

  private async run(what: string, fn: () => Promise<unknown>, done?: string): Promise<boolean> {
    this.busy.update((b) => new Set(b).add(what));
    try {
      await fn();
      if (done) this.toasts.toast({ tone: 'info', message: done });
      return true;
    } catch (err) {
      this.toasts.error(err);
      return false;
    } finally {
      this.busy.update((b) => {
        const next = new Set(b);
        next.delete(what);
        return next;
      });
    }
  }
}
