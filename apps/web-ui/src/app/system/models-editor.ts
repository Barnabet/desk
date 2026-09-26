import { ChangeDetectionStrategy, Component, computed, inject, signal, ViewEncapsulation } from '@angular/core';
import { REASONING_EFFORTS, type ModelInfo, type ReasoningEffort } from '@desk/protocol';
import { Button } from '../components/button';
import { describeError, ToastService } from '../components/toast';
import { DeskBridge } from '../core/desk-bridge';

const blank = (): ModelInfo => ({ id: '', family: 'claude', context_window: 200_000, max_output_tokens: 32_000, reasoning_efforts: [], default_reasoning_effort: null, concurrency: 4, vision: true });

/** Turns one level on or off for a model, keeping levels in order and clearing a default that is no longer offered. */
export function toggleEffort(m: ModelInfo, level: ReasoningEffort): Pick<ModelInfo, 'reasoning_efforts' | 'default_reasoning_effort'> {
  const on = new Set(m.reasoning_efforts);
  if (on.has(level)) on.delete(level);
  else on.add(level);
  const reasoning_efforts = REASONING_EFFORTS.filter((l) => on.has(l));
  return { reasoning_efforts, default_reasoning_effort: m.default_reasoning_effort && on.has(m.default_reasoning_effort) ? m.default_reasoning_effort : null };
}

/** Problems that would make PUT /models fail, in words. */
export function modelProblems(list: ModelInfo[]): string[] {
  const out: string[] = [];
  if (!list.length) out.push('Keep at least one model.');
  const ids = list.map((m) => m.id.trim());
  if (ids.some((id) => !id)) out.push('Every model needs an id.');
  const dup = ids.find((id, i) => id && ids.indexOf(id) !== i);
  if (dup) out.push(`${dup} is listed twice.`);
  if (list.some((m) => !(m.context_window > 0) || !(m.max_output_tokens > 0) || !(m.concurrency >= 1))) out.push('Token limits must be positive and concurrency at least 1.');
  return out;
}

const count = (v: string) => Math.max(0, Math.floor(Number(v) || 0));

/**
 * The model registry (PUT /models): the models Desk can pick, their limits and how many calls may run at once
 * (ModelsEditor.tsx). Until the registry is loaded the host adds no box: React renders a bare paragraph then.
 */
@Component({
  selector: 'div[deskModelsEditor]',
  imports: [Button],
  changeDetection: ChangeDetectionStrategy.OnPush,
  encapsulation: ViewEncapsulation.None,
  host: {
    '[class.models-editor]': 'ready()',
    '[style.display]': "ready() ? null : 'contents'",
  },
  template: `
    @if (error(); as message) {
      <p class="field-error">{{ message }}</p>
    } @else if (saved() === null) {
      <p class="muted">Loading…</p>
    } @else {
      <table class="models-table">
        <thead>
          <tr>
            <th scope="col">Model id</th>
            <th scope="col">Family</th>
            <th scope="col">Context</th>
            <th scope="col">Max output</th>
            <th scope="col">At once</th>
            <th scope="col"><span class="sr-only">Remove</span></th>
          </tr>
        </thead>
        <tbody>
          @for (m of draft(); track $index; let i = $index) {
            <tr class="models-main-row">
              <td><input class="input mono" [attr.aria-label]="'Model ' + (i + 1) + ' id'" [value]="m.id" (input)="set(i, { id: text($event) })" /></td>
              <td>
                <select class="select" [attr.aria-label]="'Model ' + (i + 1) + ' family'" (change)="set(i, { family: family($event) })">
                  <option value="claude" [selected]="m.family === 'claude'">claude</option>
                  <option value="gpt" [selected]="m.family === 'gpt'">gpt</option>
                </select>
              </td>
              <td><input class="input" type="number" [attr.aria-label]="'Model ' + (i + 1) + ' context window'" [value]="m.context_window" (input)="set(i, { context_window: number($event) })" /></td>
              <td><input class="input" type="number" [attr.aria-label]="'Model ' + (i + 1) + ' max output tokens'" [value]="m.max_output_tokens" (input)="set(i, { max_output_tokens: number($event) })" /></td>
              <td><input class="input narrow" type="number" [attr.aria-label]="'Model ' + (i + 1) + ' concurrency'" [value]="m.concurrency" (input)="set(i, { concurrency: number($event) })" /></td>
              <td><button type="button" class="icon-btn" [attr.aria-label]="'Remove model ' + (i + 1)" (click)="remove(i)">✕</button></td>
            </tr>
            <tr class="models-effort-row">
              <td colspan="6">
                <div class="models-effort">
                  <span class="models-effort-label">Reasoning effort</span>
                  <div class="effort-chips" role="group" [attr.aria-label]="'Model ' + (i + 1) + ' reasoning levels'">
                    @for (level of levels; track level) {
                      <button type="button" class="effort-chip" [attr.aria-pressed]="m.reasoning_efforts.includes(level)" (click)="set(i, toggle(m, level))">{{ level }}</button>
                    }
                  </div>
                  <label class="models-effort-default">Default<select class="select" [attr.aria-label]="'Model ' + (i + 1) + ' default reasoning effort'" [disabled]="!m.reasoning_efforts.length" (change)="set(i, { default_reasoning_effort: effort($event) })"><option value="" [selected]="!m.default_reasoning_effort">Endpoint default</option>@for (level of m.reasoning_efforts; track level) {<option [value]="level" [selected]="m.default_reasoning_effort === level">{{ level }}</option>}</select></label>
                  @if (!m.reasoning_efforts.length) {
                    <span class="small muted">None selected: Desk never sends a level to this model.</span>
                  }
                  <label class="models-effort-default"><input type="checkbox" [attr.aria-label]="'Model ' + (i + 1) + ' sees images'" [checked]="m.vision" (change)="set(i, { vision: checked($event) })" />Sees images</label>
                </div>
              </td>
            </tr>
          }
        </tbody>
      </table>
      @if (problems().length) {
        <ul class="field-error" role="alert">
          @for (p of problems(); track p) {
            <li>{{ p }}</li>
          }
        </ul>
      }
      <div class="actions">
        <button deskButton size="sm" (click)="add()">Add model</button>
        <button deskButton size="sm" variant="primary" [pending]="pending()" [disabled]="!dirty() || problems().length > 0" (click)="save()">Save registry</button>
        @if (dirty()) {
          <button deskButton size="sm" variant="ghost" (click)="discard()">Discard changes</button>
        }
      </div>
    }
  `,
})
export class ModelsEditor {
  private readonly bridge = inject(DeskBridge);
  private readonly toasts = inject(ToastService);
  protected readonly levels = REASONING_EFFORTS;
  protected readonly saved = signal<ModelInfo[] | null>(null);
  protected readonly draft = signal<ModelInfo[]>([]);
  protected readonly error = signal<string | null>(null);
  protected readonly pending = signal(false);
  protected readonly ready = computed(() => this.saved() !== null && this.error() === null);
  protected readonly problems = computed(() => modelProblems(this.draft()));
  protected readonly dirty = computed(() => JSON.stringify(this.draft()) !== JSON.stringify(this.saved()));

  constructor() {
    this.bridge.call('models.list', {}).then(
      (m) => {
        this.saved.set(m);
        this.draft.set(m);
      },
      (err: unknown) => this.error.set(describeError(err).message),
    );
  }

  protected set(i: number, patch: Partial<ModelInfo>): void {
    this.draft.update((d) => d.map((m, k) => (k === i ? { ...m, ...patch } : m)));
  }

  protected toggle(m: ModelInfo, level: ReasoningEffort): Pick<ModelInfo, 'reasoning_efforts' | 'default_reasoning_effort'> {
    return toggleEffort(m, level);
  }

  protected remove(i: number): void {
    this.draft.update((d) => d.filter((_, k) => k !== i));
  }

  protected add(): void {
    this.draft.update((d) => [...d, blank()]);
  }

  protected discard(): void {
    this.draft.set(this.saved() ?? []);
  }

  protected async save(): Promise<void> {
    this.pending.set(true);
    try {
      const next = await this.bridge.call('models.replace', { models: this.draft().map((m) => ({ ...m, id: m.id.trim() })) });
      this.saved.set(next);
      this.draft.set(next);
      this.toasts.toast({ tone: 'info', message: 'Model registry saved.' });
    } catch (err) {
      this.toasts.error(err);
    } finally {
      this.pending.set(false);
    }
  }

  protected text(e: Event): string {
    return (e.target as HTMLInputElement).value;
  }

  /**
   * The whole number a box holds. Angular writes `[value]` only when it changes, so a box whose text reads as the number
   * it already held (4.5 over 4, or 2.2 after 2.9) is rewritten here, as React's controlled number input does.
   */
  protected number(e: Event): number {
    const el = e.target as HTMLInputElement;
    const n = count(el.value);
    if (el.value === '' ? n === 0 : Number(el.value) !== n) el.value = String(n);
    return n;
  }

  protected checked(e: Event): boolean {
    return (e.target as HTMLInputElement).checked;
  }

  protected family(e: Event): ModelInfo['family'] {
    return (e.target as HTMLSelectElement).value as ModelInfo['family'];
  }

  protected effort(e: Event): ReasoningEffort | null {
    return ((e.target as HTMLSelectElement).value || null) as ReasoningEffort | null;
  }
}
