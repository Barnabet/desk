import { ChangeDetectionStrategy, Component, computed, input, output, ViewEncapsulation } from '@angular/core';
import { conditionSuggestions, edgeRouteOptions, patchEdge, type AutomationDoc } from '@desk/ui-core';
import { Button } from '../../components/button';
import { Field } from '../../components/field';

/** The selected edge (spec §3.2): what its source must do for it to fire, and an optional condition. */
@Component({
  selector: 'aside[deskEdgeInspector]',
  imports: [Button, Field],
  changeDetection: ChangeDetectionStrategy.OnPush,
  encapsulation: ViewEncapsulation.None,
  host: { class: 'auto-inspector', 'aria-label': 'Edge' },
  template: `
    @if (edge(); as edge) {
      <p class="eyebrow">Edge</p>
      <h2>{{ title(edge.from) + ' → ' + title(edge.to) }}</h2>
      @if (errors().length) {
        <ul class="auto-issues" role="alert">
          @for (e of errors(); track e) {
            <li>{{ e }}</li>
          }
        </ul>
      }
      <div deskField id="edge-route" label="Fires">
        <select id="edge-route" class="select" (change)="set({ route: val($event) || undefined })">
          @for (r of routeOptions(); track r.value) {
            <option [value]="r.value" [selected]="r.value === (edge.route ?? '')">{{ r.label }}</option>
          }
        </select>
      </div>
      <div deskField id="edge-when" label="Only if" [hint]="whenHint">
        <input id="edge-when" class="input mono" [value]="edge.when ?? ''" (input)="setWhen(val($event))" />
      </div>
      @if (paths().length) {
        <div class="auto-paths" aria-label="Paths it can use">
          @for (p of paths(); track p.path) {
            <button type="button" class="auto-path" [attr.title]="p.label" (click)="append(p.path)">{{ p.path }}</button>
          }
        </div>
      }
      <div><button deskButton variant="danger" size="sm" (click)="remove.emit()">Delete edge</button></div>
    }
  `,
})
export class EdgeInspector {
  readonly doc = input.required<AutomationDoc>();
  readonly index = input.required<number>();
  readonly errors = input.required<string[]>();
  /** React's onChange. */
  readonly docChange = output<AutomationDoc>();
  /** React's onDelete. */
  readonly remove = output<void>();

  protected readonly whenHint = 'Optional, e.g. steps.fetch.outputs.count > 0. Use == != < <= > >= contains exists(…) and or not.';
  protected readonly edge = computed(() => this.doc().def.edges[this.index()]);
  protected readonly routeOptions = computed(() => {
    const e = this.edge();
    return e ? edgeRouteOptions(this.doc().def, e.from, e.route) : [];
  });
  protected readonly paths = computed(() => {
    const e = this.edge();
    return e ? conditionSuggestions(this.doc().def, e.from).filter((s) => !s.open) : [];
  });

  protected val(e: Event): string {
    return (e.target as HTMLInputElement).value;
  }

  protected title(id: string): string {
    return this.doc().def.steps.find((s) => s.id === id)?.title ?? id;
  }

  protected set(p: { route?: string | undefined; when?: string | undefined }): void {
    this.docChange.emit(patchEdge(this.doc(), this.index(), p));
  }

  protected setWhen(v: string): void {
    this.set({ when: v.trim() ? v : undefined });
  }

  protected append(path: string): void {
    const when = this.edge()?.when;
    this.set({ when: when ? `${when.trimEnd()} ${path}` : path });
  }
}
