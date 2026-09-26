import { NgTemplateOutlet } from '@angular/common';
import { ChangeDetectionStrategy, Component, ViewEncapsulation, computed, input, output, signal, type Signal } from '@angular/core';
import type { CatalogInstall, CatalogItem } from '@desk/protocol';
import { href, skillKey } from '@desk/ui-core';
import { TemplateOf, templateOf } from '../../components/template-of';
import { actionFor, BAYS, installRef, runtimeWords, sourceLabel } from './data';
import { RuntimeLine } from './runtime-line';

export type Layout = 'cards' | 'list';

const LAYOUT_KEY = 'desk.catalogLayout';

/** Cards or compact rows, remembered per viewer (the React `useCatalogLayout`). */
export function injectCatalogLayout(): { layout: Signal<Layout>; set(next: Layout): void } {
  let initial: Layout = 'cards';
  try {
    initial = localStorage.getItem(LAYOUT_KEY) === 'list' ? 'list' : 'cards';
  } catch {
    // A convenience only.
  }
  const layout = signal<Layout>(initial);
  return {
    layout: layout.asReadonly(),
    set(next) {
      layout.set(next);
      try {
        localStorage.setItem(LAYOUT_KEY, next);
      } catch {
        // A convenience only.
      }
    },
  };
}

/** The Cards / Compact switch, shown with the Skills screen's controls. */
@Component({
  selector: 'div[deskLayoutSwitch]',
  changeDetection: ChangeDetectionStrategy.OnPush,
  encapsulation: ViewEncapsulation.None,
  host: { class: 'segmented', role: 'group', 'aria-label': 'Layout' },
  template: `
    <button type="button" [attr.aria-pressed]="layout() === 'cards'" (click)="changed.emit('cards')">Cards</button>
    <button type="button" [attr.aria-pressed]="layout() === 'list'" (click)="changed.emit('list')">Compact</button>
  `,
})
export class LayoutSwitch {
  readonly layout = input.required<Layout>();
  /** The layout picked (React's `onChange`). */
  readonly changed = output<Layout>();
}

type ActionView =
  | { kind: 'installed'; href: string }
  | { kind: 'taken'; title: string }
  | { kind: 'button'; id: string; tone: 'btn-primary' | 'btn-secondary'; ariaLabel: string; text: string };

type EntryView = {
  item: CatalogItem;
  source: string;
  sourceTone: 'chip-done' | 'chip-idle';
  scripts: string | null;
  words: string;
  /** The global install, when its runtime line shows on the card. */
  runtime: CatalogInstall | null;
  also: string | null;
  action: ActionView;
};

/** Where else an entry is installed, e.g. "In Thesis". */
function elsewhere(item: CatalogItem, projectNames: Map<string, string>): string | null {
  const names = item.installs.filter((i) => i.scope === 'project' && i.state !== 'name_taken').map((i) => projectNames.get(i.project_id!) ?? 'a project');
  return names.length ? `In ${names.join(', ')}` : null;
}

/** The React `Action` for the global scope: a link to the installed skill, the "Name taken" chip, or a button that opens the review. */
function actionView(item: CatalogItem, install: CatalogInstall | undefined): ActionView {
  const a = actionFor(install);
  if (a.kind === 'installed' && install) return { kind: 'installed', href: href({ name: 'skills', skill: skillKey(installRef(item.id, install)) }) };
  if (a.kind === 'taken') return { kind: 'taken', title: `You already have a skill named ${item.id} that didn't come from the catalog.` };
  return {
    kind: 'button',
    id: item.id,
    tone: a.kind === 'install' ? 'btn-primary' : 'btn-secondary',
    ariaLabel: a.kind === 'modified' ? `Review ${item.title} (edited since install)` : `${a.label} ${item.title}`,
    text: a.kind === 'modified' ? 'Modified · review' : a.label,
  };
}

function entryView(item: CatalogItem, projectNames: Map<string, string>): EntryView {
  const global = item.installs.find((i) => i.scope === 'global');
  return {
    item,
    source: sourceLabel(item),
    sourceTone: item.source.type === 'builtin' ? 'chip-done' : 'chip-idle',
    scripts: item.scripts ? (item.scripts === 1 ? '1 script' : `${item.scripts} scripts`) : null,
    words: runtimeWords(item),
    runtime: global && global.state !== 'name_taken' ? global : null,
    also: elsewhere(item, projectNames),
    action: actionView(item, global),
  };
}

/** The catalog in bays (one per category): cards (or a list) with source, licence, scripts, runtime and an action per entry (CatalogView.tsx). */
@Component({
  selector: 'div[deskCatalogView]',
  imports: [NgTemplateOutlet, TemplateOf, RuntimeLine],
  changeDetection: ChangeDetectionStrategy.OnPush,
  encapsulation: ViewEncapsulation.None,
  host: { class: 'catalog' },
  template: `
    <ng-template #action let-a [deskTemplateOf]="actionType">
      @switch (a.kind) {
        @case ('installed') {
          <a class="btn btn-ghost btn-sm catalog-installed" [href]="a.href">✓ Installed</a>
        }
        @case ('taken') {
          <span class="chip chip-idle" [title]="a.title">Name taken</span>
        }
        @default {
          <button type="button" class="btn btn-sm" [class]="a.tone" [attr.aria-label]="a.ariaLabel" (click)="review.emit(a.id)">{{ a.text }}</button>
        }
      }
    </ng-template>
    @for (bay of bays(); track bay.category) {
      <section class="catalog-bay" [attr.aria-labelledby]="'bay-' + bay.category">
        <header class="catalog-bay-head">
          <h2 [id]="'bay-' + bay.category">{{ bay.title }}</h2>
          <span class="muted small">{{ bay.blurb }} · {{ bay.entries.length }}</span>
        </header>
        @if (layout() === 'cards') {
          <ul class="catalog-cards">
            @for (v of bay.entries; track v.item.id) {
              <li class="catalog-card card" [attr.aria-label]="v.item.title">
                <button type="button" class="catalog-card-open" (click)="review.emit(v.item.id)">
                  <span class="catalog-card-title">{{ v.item.title }}</span>
                  <span class="catalog-card-summary">{{ v.item.summary }}</span>
                </button>
                <div class="catalog-chips">
                  <span class="chip" [class]="v.sourceTone">{{ v.source }}</span>
                  <span class="chip chip-idle">{{ v.item.license }}</span>
                  @if (v.scripts) {
                    <span class="chip chip-wait">{{ v.scripts }}</span>
                  }
                </div>
                <p class="catalog-runtime small muted">{{ v.words }}</p>
                @if (v.runtime; as rt) {
                  <div deskRuntimeLine [entry]="v.item" [install]="rt"></div>
                }
                <div class="catalog-card-foot">
                  <span class="small muted grow">{{ v.also }}</span>
                  <ng-container [ngTemplateOutlet]="action" [ngTemplateOutletContext]="{ $implicit: v.action }" />
                </div>
              </li>
            }
          </ul>
        } @else {
          <ul class="catalog-rows">
            @for (v of bay.entries; track v.item.id) {
              <li class="catalog-row">
                <button type="button" class="catalog-row-open" (click)="review.emit(v.item.id)">
                  <span class="catalog-row-title">{{ v.item.title }}</span>
                  <span class="muted small grow">{{ v.item.summary }}</span>
                </button>
                <span class="small muted catalog-row-meta">{{ v.source }} · {{ v.item.license }}{{ v.scripts ? ' · ' + v.scripts : '' }}{{ v.also ? ' · ' + v.also : '' }}</span>
                <ng-container [ngTemplateOutlet]="action" [ngTemplateOutletContext]="{ $implicit: v.action }" />
              </li>
            }
          </ul>
        }
      </section>
    }
  `,
})
export class CatalogView {
  readonly items = input.required<CatalogItem[]>();
  readonly layout = input.required<Layout>();
  readonly projectNames = input.required<Map<string, string>>();
  /** The entry to review (React's `onReview`). */
  readonly review = output<string>();
  protected readonly actionType = templateOf<ActionView>();

  protected readonly bays = computed(() => {
    const names = this.projectNames();
    const items = this.items();
    return BAYS.map((bay) => ({ ...bay, entries: items.filter((i) => i.category === bay.category).map((item) => entryView(item, names)) })).filter((bay) => bay.entries.length > 0);
  });
}
