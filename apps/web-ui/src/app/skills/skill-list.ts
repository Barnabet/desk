import { ChangeDetectionStrategy, Component, ViewEncapsulation, computed, input, output } from '@angular/core';
import type { SkillNode } from '@desk/client';

const byName = (a: SkillNode, b: SkillNode) => a.name.localeCompare(b.name);

/** The list alternative to the map: global skills, then each project's own (SkillList.tsx). */
@Component({
  selector: 'div[deskSkillList]',
  changeDetection: ChangeDetectionStrategy.OnPush,
  encapsulation: ViewEncapsulation.None,
  host: { class: 'skill-list' },
  template: `
    @for (g of groups(); track $index) {
      <section [attr.aria-label]="g.title + ' skills'">
        <h2 class="skill-list-title">{{ g.title }}</h2>
        @if (g.nodes.length) {
          <ul>
            @for (n of g.nodes; track n.key) {
              <li>
                <button type="button" class="skill-row" [class.current]="selected() === n.key" [attr.aria-pressed]="selected() === n.key" (click)="selectSkill.emit(n.key)">
                  <span class="mono skill-row-name">{{ n.name }}</span>
                  <span class="muted small grow">{{ n.error ? 'Broken: ' + n.error : n.description }}</span>
                  @if (catalogKeys()?.has(n.key)) {
                    <span class="chip chip-done">from catalog</span>
                  }
                  @if (n.shadows) {
                    <span class="chip chip-wait">shadows global</span>
                  }
                  @if (n.shadowedIn.length) {
                    <span class="chip chip-idle">shadowed in {{ n.shadowedIn.length }}</span>
                  }
                  @if (n.usedBy.length) {
                    <span class="chip chip-run">in use · {{ n.usedBy.length }}</span>
                  }
                  <span class="mono small muted">v{{ n.version }}</span>
                </button>
              </li>
            }
          </ul>
        } @else {
          <p class="muted small">No global skills yet.</p>
        }
      </section>
    }
  `,
})
export class SkillList {
  readonly nodes = input.required<SkillNode[]>();
  readonly projectNames = input.required<Map<string, string>>();
  /** Keys of skills installed from the catalog. */
  readonly catalogKeys = input<ReadonlySet<string>>();
  readonly selected = input<string | null>(null);
  /** The skill picked (React's `onSelect`). */
  readonly selectSkill = output<string>();

  protected readonly groups = computed(() => {
    const nodes = this.nodes();
    const out: Array<{ title: string; nodes: SkillNode[] }> = [{ title: 'Global', nodes: nodes.filter((n) => n.scope === 'global').sort(byName) }];
    for (const [id, name] of this.projectNames()) {
      const own = nodes.filter((n) => n.scope === 'project' && n.projectId === id);
      if (own.length) out.push({ title: name, nodes: own.sort(byName) });
    }
    return out;
  });
}
