import { ChangeDetectionStrategy, Component, computed, input, output, ViewEncapsulation } from '@angular/core';
import type { AttentionItem } from '@desk/protocol';
import { gauge, STRIP_CODE, stripWho, waited } from '@desk/ui-core';

/** The wait gauge's colour: vermilion for approvals and failures, amber for holds (stalled threads, paused projects). */
const CAP_COLOR: Record<AttentionItem['kind'], string> = {
  approval: 'var(--accent)',
  question: 'var(--ink)',
  needs_you: 'var(--ink)',
  stalled: 'var(--wait)',
  failed: 'var(--accent)',
  paused: 'var(--wait)',
};

/** One flight strip: end cap (code and age), project and title, who, the wait gauge, and a chevron. */
@Component({
  selector: 'button[deskFlightStrip]',
  changeDetection: ChangeDetectionStrategy.OnPush,
  encapsulation: ViewEncapsulation.None,
  host: {
    type: 'button',
    class: 'strip',
    '[class]': "'strip-' + item().kind",
    '[class.selected]': 'selected()',
    '[class.compact]': 'compact()',
    '[attr.aria-current]': "selected() ? 'true' : null",
    '[attr.aria-label]': 'label()',
    '(click)': 'pick.emit()',
  },
  template: `
    <span class="strip-cap" aria-hidden="true"><span class="strip-code">{{ code() }}</span><span class="strip-age">{{ age() }}</span></span>
    <span class="strip-title" aria-hidden="true"><span class="strip-project">{{ item().project_name }}</span><span class="strip-text">{{ item().title }}</span></span>
    @if (!compact()) {
      <span class="strip-col strip-who" aria-hidden="true"><span class="strip-label">{{ who().label }}</span><span class="strip-name">{{ who().name }}</span><span class="strip-tag" [class.wait]="waits()">{{ who().tag }}</span></span>
      <span class="strip-col strip-wait" aria-hidden="true">
        <span class="strip-label">Waiting</span>
        <svg width="76" height="10" viewBox="0 0 76 10">
          <rect x="0" y="3" width="76" height="4" rx="2" fill="#E6E0D4" />
          <path d="M38 1 V9 M75.5 1 V9" stroke="#8A857B" stroke-width="1" />
          <rect x="0" y="3" [attr.width]="barWidth()" height="4" rx="2" [attr.fill]="capColor()" />
        </svg>
        <span class="strip-age-big">{{ age() }}</span>
      </span>
    }
    <span class="strip-chevron" aria-hidden="true">›</span>
  `,
})
export class FlightStrip {
  readonly item = input.required<AttentionItem>();
  readonly now = input.required<number>();
  readonly selected = input(false);
  readonly threadTitle = input.required<(id: string) => string | null>();
  readonly compact = input(false);
  /** React's onSelect. */
  readonly pick = output<void>();

  protected readonly code = computed(() => STRIP_CODE[this.item().kind]);
  protected readonly who = computed(() => stripWho(this.item(), this.threadTitle()));
  protected readonly age = computed(() => waited(this.item().created_at, this.now()));
  protected readonly barWidth = computed(() => Math.max(3, 76 * gauge(this.item().created_at, this.now())));
  protected readonly capColor = computed(() => CAP_COLOR[this.item().kind]);
  /** A stalled or paused strip's tag reads as a wait. */
  protected readonly waits = computed(() => {
    const kind = this.item().kind;
    return kind === 'stalled' || kind === 'paused';
  });
  protected readonly label = computed(() => {
    const i = this.item();
    const who = this.who();
    return `${STRIP_CODE[i.kind]}, ${i.project_name}: ${i.title}. ${who.label} ${who.name}. Waiting ${this.age()}.`;
  });
}
