import { ChangeDetectionStrategy, Component, input, ViewEncapsulation } from '@angular/core';
import { STEP_KIND_LABEL, type DefinitionDiff, type FieldChange } from '@desk/ui-core';

const TAG = { added: 'added', removed: 'removed', changed: 'changed' } as const;

/** A table of field changes: the field, its value before (red) and after (green). */
@Component({
  selector: 'table[deskDiffFields]',
  changeDetection: ChangeDetectionStrategy.OnPush,
  encapsulation: ViewEncapsulation.None,
  host: { class: 'auto-diff-fields' },
  template: `
    <tbody>
      @for (f of fields(); track f.field) {
        <tr>
          <th class="mono">{{ f.field }}</th>
          <td class="auto-before">{{ f.before }}</td>
          <td class="auto-after">{{ f.after }}</td>
        </tr>
      }
    </tbody>
  `,
})
export class Fields {
  readonly fields = input.required<FieldChange[]>();
}

/** A structured diff between two definitions (spec §8.4). Everything is plain text. */
@Component({
  selector: 'div[deskDiffView]',
  imports: [Fields],
  changeDetection: ChangeDetectionStrategy.OnPush,
  encapsulation: ViewEncapsulation.None,
  host: { style: 'display: contents' },
  template: `
    @if (diff().empty) {
      <p class="muted">No changes.</p>
    } @else {
      <div class="auto-diff">
        @if (labels(); as l) {
          <p class="muted small">{{ 'Red is ' + l.before + ', green is ' + l.after + '.' }}</p>
        }
        @if (diff().steps.length) {
          <section>
            <h3 class="auto-sub">Steps</h3>
            <ul class="auto-plain">
              @for (s of diff().steps; track s.id) {
                <li class="auto-diff-item">
                  <span class="auto-tag" [class]="tag[s.change]">{{ s.change }}</span>&ngsp;<b>{{ s.title }}</b>&ngsp;<span class="muted small">{{ kindLabel[s.kind] + ' · ' + s.id }}</span>
                  @if (s.fields.length) {
                    <table deskDiffFields [fields]="s.fields"></table>
                  }
                </li>
              }
            </ul>
          </section>
        }
        @if (diff().edges.added.length || diff().edges.removed.length) {
          <section>
            <h3 class="auto-sub">Edges</h3>
            <ul class="auto-plain">
              @for (e of diff().edges.removed; track e) {
                <li class="auto-diff-item"><span class="auto-tag removed">removed</span>&ngsp;<span>{{ e }}</span></li>
              }
              @for (e of diff().edges.added; track e) {
                <li class="auto-diff-item"><span class="auto-tag added">added</span>&ngsp;<span>{{ e }}</span></li>
              }
            </ul>
          </section>
        }
        @if (diff().inputs.length) {
          <section>
            <h3 class="auto-sub">Inputs</h3>
            <ul class="auto-plain">
              @for (i of diff().inputs; track i.key) {
                <li class="auto-diff-item">
                  <span class="auto-tag" [class]="tag[i.change]">{{ i.change }}</span>&ngsp;<b>{{ i.label }}</b>&ngsp;<span class="muted small mono">{{ i.key }}</span>
                  @if (i.fields.length) {
                    <table deskDiffFields [fields]="i.fields"></table>
                  }
                </li>
              }
            </ul>
          </section>
        }
        @if (diff().schedules; as sched) {
          <section>
            <h3 class="auto-sub">Schedules</h3>
            <p class="auto-before">{{ sched.before.join(' · ') || 'Run now only' }}</p>
            <p class="auto-after">{{ sched.after.join(' · ') || 'Run now only' }}</p>
          </section>
        }
        @if (diff().settings.length) {
          <section>
            <h3 class="auto-sub">Settings</h3>
            <table deskDiffFields [fields]="diff().settings"></table>
          </section>
        }
      </div>
    }
  `,
})
export class DiffView {
  readonly diff = input.required<DefinitionDiff>();
  readonly labels = input<{ before: string; after: string } | undefined>(undefined);
  protected readonly tag = TAG;
  protected readonly kindLabel = STEP_KIND_LABEL;
}
