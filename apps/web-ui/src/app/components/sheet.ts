import { afterNextRender, ChangeDetectionStrategy, Component, DestroyRef, ElementRef, inject, input, output, viewChild, ViewEncapsulation } from '@angular/core';

let nextTitle = 0;

/**
 * A modal dialog: focus moves in, Escape or a backdrop click closes, focus returns on close. Like the React portal, the
 * backdrop (this host) lives in document.body. A projected `.sheet-footer` element is the footer. Escape closes only
 * the topmost sheet (the folder browser, not the project form under it).
 */
@Component({
  selector: 'div[deskSheet]',
  changeDetection: ChangeDetectionStrategy.OnPush,
  encapsulation: ViewEncapsulation.None,
  host: {
    class: 'sheet-backdrop',
    '[attr.title]': 'null',
    '(mousedown)': 'onBackdrop($event)',
    '(window:keydown)': 'onKey($event)',
  },
  template: `
    <div #panel class="sheet" role="dialog" aria-modal="true" [attr.aria-labelledby]="titleId" [style.width.px]="width()">
      <h2 [id]="titleId" class="sheet-title">{{ title() }}</h2>
      <div class="sheet-body"><ng-content /></div>
      <ng-content select=".sheet-footer" />
    </div>
  `,
})
export class Sheet {
  readonly title = input.required<string>();
  readonly width = input(520);
  readonly close = output<void>();
  protected readonly titleId = `sheet-title-${++nextTitle}`;
  private readonly host: HTMLElement = inject<ElementRef<HTMLElement>>(ElementRef).nativeElement;
  private readonly panel = viewChild.required<ElementRef<HTMLElement>>('panel');

  constructor() {
    const previous = document.activeElement instanceof HTMLElement ? document.activeElement : null;
    // After the first render, so the view that holds this host has been inserted; then Angular leaves it where it is.
    afterNextRender(() => {
      document.body.appendChild(this.host);
      this.panel().nativeElement.querySelector<HTMLElement>('input, textarea, select, button')?.focus();
    });
    inject(DestroyRef).onDestroy(() => {
      this.host.remove();
      previous?.focus();
    });
  }

  protected onBackdrop(e: MouseEvent): void {
    if (e.target === this.host) this.close.emit();
  }

  protected onKey(e: KeyboardEvent): void {
    if (e.key === 'Escape' && this.isTopmost()) this.close.emit();
  }

  /** Each sheet appends its backdrop to the body when it opens, so the one on top is the body's last backdrop. */
  private isTopmost(): boolean {
    const backdrops = [...document.body.children].filter((el) => el.classList.contains('sheet-backdrop'));
    return backdrops.at(-1) === this.host;
  }
}
