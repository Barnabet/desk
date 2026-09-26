import { ChangeDetectionStrategy, Component, DestroyRef, ViewEncapsulation, booleanAttribute, computed, inject, input, output, signal } from '@angular/core';
import { runtimeKey } from '@desk/bff/contract';
import type { BuiltinSkillInfo } from '@desk/protocol';
import { ToastService } from '../../components/toast';
import { DeskBridge } from '../../core/desk-bridge';
import { GlobalStore } from '../../core/global.store';
import { SkillsRefresh } from '../skills-refresh';
import { builtinKey, runtimeLabel } from './data';

const OPEN_KEY = 'desk.builtinsOpen';

/** The on/off switch of a built-in skill; `label` names the skill for screen readers (BuiltinGroup.tsx `BuiltinSwitch`). */
@Component({
  selector: 'button[deskBuiltinSwitch]',
  changeDetection: ChangeDetectionStrategy.OnPush,
  encapsulation: ViewEncapsulation.None,
  host: {
    type: 'button',
    role: 'switch',
    class: 'switch',
    '[attr.aria-checked]': 'item().enabled',
    '[attr.aria-label]': 'label()',
    '[attr.title]': "item().enabled ? 'On: agents can use it' : 'Off: agents never see it'",
    '[disabled]': 'pending()',
    '(click)': 'toggle()',
  },
  template: `<span class="switch-knob" aria-hidden="true"></span>`,
})
export class BuiltinSwitch {
  readonly item = input.required<BuiltinSkillInfo>();
  readonly label = input.required<string>();
  readonly changed = output<void>();
  private readonly bridge = inject(DeskBridge);
  private readonly toasts = inject(ToastService);
  /** The skills screen, told directly about a switch that lands after its host (the group or the panel) closed. */
  private readonly screen = inject(SkillsRefresh, { optional: true });
  /** Set once the switch is gone (its group or panel closed while the call ran): `changed` then reaches no one. */
  private closed = false;
  /** The built-ins whose switch call is running: the panel's switch shows only its own skill's, whichever one it shows now. */
  private readonly running = signal<ReadonlySet<string>>(new Set());
  protected readonly pending = computed(() => this.running().has(this.item().name));

  constructor() {
    inject(DestroyRef).onDestroy(() => (this.closed = true));
  }

  protected async toggle(): Promise<void> {
    const item = this.item();
    this.running.update((r) => new Set(r).add(item.name));
    try {
      await this.bridge.call('builtins.setEnabled', { name: item.name, enabled: !item.enabled });
      // `changed` while the switch is shown, else the screen itself (never both).
      if (this.closed) this.screen?.changed();
      else this.changed.emit();
    } catch (err) {
      this.toasts.error(err);
    } finally {
      this.running.update((r) => {
        const next = new Set(r);
        next.delete(item.name);
        return next;
      });
    }
  }
}

/** A built-in skill's environment line, with live progress while it is being set up (BuiltinGroup.tsx `BuiltinRuntime`). */
@Component({
  selector: 'span[deskBuiltinRuntime]',
  changeDetection: ChangeDetectionStrategy.OnPush,
  encapsulation: ViewEncapsulation.None,
  host: { class: 'runtime-line', '[class]': 'state()' },
  template: `<span class="runtime-dot" aria-hidden="true"></span>{{ text() }}`,
})
export class BuiltinRuntime {
  readonly item = input.required<BuiltinSkillInfo>();
  private readonly global = inject(GlobalStore);
  protected readonly state = computed(() => (this.item().broken ? 'failed' : this.item().runtime.state));
  /** This built-in's live setup step; an own key only, since the name comes from deskd. */
  private readonly progress = computed(() => {
    const all = this.global.state().runtimes.progress;
    const key = runtimeKey('builtin', null, this.item().name);
    return Object.hasOwn(all, key) ? all[key] : undefined;
  });
  protected readonly text = computed(() => {
    const item = this.item();
    if (item.broken) return 'Damaged: reinstall Desk';
    return runtimeLabel(item, this.progress());
  });
}

/**
 * Desk's own skills, above the user's: a card each with its environment and an on/off switch. `collapsible` (the map
 * view) folds the cards into one line until opened (BuiltinGroup.tsx).
 */
@Component({
  selector: 'section[deskBuiltinGroup]',
  imports: [BuiltinRuntime, BuiltinSwitch],
  changeDetection: ChangeDetectionStrategy.OnPush,
  encapsulation: ViewEncapsulation.None,
  host: { class: 'builtin-group', 'aria-label': 'Built into Desk' },
  template: `
    <header class="builtin-head">
      <h2>Built into Desk</h2>
      <span class="muted small grow">Any file type: read, create, edit, convert — and see it; plus web research · {{ items().length }}{{ off() ? ' · ' + off() + ' off' : '' }}</span>
      @if (collapsible()) {
        <button type="button" class="builtin-fold" [attr.aria-expanded]="shown()" (click)="flip()">{{ shown() ? 'Hide' : 'Show' }}</button>
      }
    </header>
    @if (shown()) {
      <ul class="builtin-cards">
        @for (b of items(); track b.name) {
          <li class="card builtin-card" [class.selected]="selected() === key(b.name)" [class.off]="!b.enabled || !!b.broken">
            <button type="button" class="builtin-open" [attr.aria-label]="'Open ' + b.title" (click)="selectSkill.emit(key(b.name))">
              <span class="builtin-title">{{ b.title }}</span>
              <span class="builtin-meta"><span class="chip chip-idle">Built in</span>@if (b.shadowed_by) {<span class="chip chip-wait">Shadowed by your {{ b.shadowed_by }} skill</span>}</span>
              <span deskBuiltinRuntime [item]="b"></span>
            </button>
            @if (!b.broken) {
              <button deskBuiltinSwitch [item]="b" [label]="b.title" (changed)="changed.emit()"></button>
            }
          </li>
        }
      </ul>
    }
  `,
})
export class BuiltinGroup {
  readonly items = input.required<BuiltinSkillInfo[]>();
  readonly selected = input.required<string | null>();
  readonly collapsible = input(false, { transform: booleanAttribute });
  /** A card was opened (React's `onSelect`). */
  readonly selectSkill = output<string>();
  readonly changed = output<void>();
  protected readonly key = builtinKey;
  private readonly open = signal(readOpen());
  protected readonly shown = computed(() => !this.collapsible() || this.open());
  protected readonly off = computed(() => this.items().filter((b) => !b.enabled).length);

  protected flip(): void {
    const next = !this.open();
    this.open.set(next);
    try {
      localStorage.setItem(OPEN_KEY, String(next));
    } catch {
      // A convenience only.
    }
  }
}

function readOpen(): boolean {
  try {
    return localStorage.getItem(OPEN_KEY) !== 'false';
  } catch {
    return true;
  }
}
