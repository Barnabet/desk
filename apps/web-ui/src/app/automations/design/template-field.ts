import { booleanAttribute, ChangeDetectionStrategy, Component, computed, ElementRef, input, output, signal, viewChild, ViewEncapsulation } from '@angular/core';
import { activeToken, filterSuggestions, insertSuggestion, isKnownPath, pathsIn, type TemplateSuggestion } from '@desk/ui-core';

type Box = HTMLInputElement | HTMLTextAreaElement;
let nextList = 0;

/** A text field with `{{…}}` autocomplete (inputs, upstream steps' results, the run), showing the paths it uses as chips (spec §8.2). */
@Component({
  selector: 'div[deskTemplateField]',
  changeDetection: ChangeDetectionStrategy.OnPush,
  encapsulation: ViewEncapsulation.None,
  host: { class: 'field auto-template', '[attr.id]': 'null' },
  template: `
    <label [attr.for]="id()" [class.sr-only]="labelHidden()">{{ label() }}</label>
    @if (multiline()) {
      <textarea
        #box
        class="textarea"
        role="combobox"
        aria-autocomplete="list"
        [id]="id()"
        [rows]="rows()"
        [value]="value()"
        [attr.placeholder]="placeholder() ?? null"
        [attr.aria-expanded]="open()"
        [attr.aria-controls]="listId"
        (input)="onInput($event)"
        (keydown)="onKeyDown($event)"
        (keyup)="track($event)"
        (click)="track($event)"
        (blur)="onBlur()"
      ></textarea>
    } @else {
      <input
        #box
        class="input"
        role="combobox"
        aria-autocomplete="list"
        [id]="id()"
        [value]="value()"
        [attr.placeholder]="placeholder() ?? null"
        [attr.aria-expanded]="open()"
        [attr.aria-controls]="listId"
        (input)="onInput($event)"
        (keydown)="onKeyDown($event)"
        (keyup)="track($event)"
        (click)="track($event)"
        (blur)="onBlur()"
      />
    }
    @if (open()) {
      <ul role="listbox" class="auto-suggest" [id]="listId">
        @for (s of options(); track s.path; let i = $index) {
          <li role="option" [attr.aria-selected]="i === active()" (mousedown)="$event.preventDefault(); choose(s)"><span class="mono">{{ s.open ? s.path + '…' : s.path }}</span><span class="muted small">{{ s.label }}</span></li>
        }
      </ul>
    }
    @if (chips().length) {
      <div class="auto-chips">
        @for (c of chips(); track c.path) {
          <span class="auto-tpl" [class.unknown]="!c.known" [attr.title]="c.known ? null : 'Not available here'">{{ c.text }}</span>
        }
      </div>
    }
    @if (errors().length) {
      @for (e of errors(); track e) {
        <p class="field-error" role="alert">{{ e }}</p>
      }
    } @else if (hint()) {
      <p class="field-hint">{{ hint() }}</p>
    }
  `,
})
export class TemplateField {
  readonly id = input.required<string>();
  readonly label = input.required<string>();
  readonly value = input.required<string>();
  readonly suggestions = input.required<TemplateSuggestion[]>();
  readonly multiline = input(false, { transform: booleanAttribute });
  readonly rows = input(4);
  readonly placeholder = input<string | undefined>(undefined);
  readonly hint = input<string | undefined>(undefined);
  readonly errors = input<string[]>([]);
  /** Keeps the label for screen readers only (a row of a list). */
  readonly labelHidden = input(false, { transform: booleanAttribute });
  /** React's onChange. */
  readonly valueChange = output<string>();

  private readonly box = viewChild<ElementRef<Box>>('box');
  protected readonly listId = `auto-suggest-${++nextList}`;
  protected readonly caret = signal<number | null>(null);
  protected readonly active = signal(0);
  private readonly query = computed(() => {
    const c = this.caret();
    return c === null ? null : (activeToken(this.value(), c)?.query ?? null);
  });
  protected readonly options = computed(() => {
    const q = this.query();
    return q === null ? [] : filterSuggestions(this.suggestions(), q);
  });
  protected readonly open = computed(() => this.options().length > 0);
  /** The `{{…}}` paths the value uses, each marked when this step cannot see it. */
  protected readonly chips = computed(() => pathsIn(this.value()).map((path) => ({ path, text: `{{${path}}}`, known: isKnownPath(path, this.suggestions()) })));

  protected choose(s: TemplateSuggestion): void {
    const next = insertSuggestion(this.value(), this.caret() ?? this.value().length, s);
    this.valueChange.emit(next.text);
    this.caret.set(s.open ? next.caret : null);
    this.active.set(0);
    requestAnimationFrame(() => {
      const el = this.box()?.nativeElement;
      el?.focus();
      el?.setSelectionRange(next.caret, next.caret);
    });
  }

  protected track(e: Event): void {
    this.caret.set((e.target as Box).selectionStart);
  }

  protected onInput(e: Event): void {
    const el = e.target as Box;
    this.valueChange.emit(el.value);
    this.caret.set(el.selectionStart);
    this.active.set(0);
  }

  protected onKeyDown(e: KeyboardEvent): void {
    const options = this.options();
    if (!options.length) return;
    if (e.key === 'ArrowDown' || e.key === 'ArrowUp') {
      e.preventDefault();
      this.active.update((a) => (a + (e.key === 'ArrowDown' ? 1 : options.length - 1)) % options.length);
    } else if (e.key === 'Enter' || e.key === 'Tab') {
      e.preventDefault();
      const s = options[Math.min(this.active(), options.length - 1)];
      if (s) this.choose(s);
    } else if (e.key === 'Escape') {
      e.preventDefault();
      this.caret.set(null);
    }
  }

  protected onBlur(): void {
    setTimeout(() => this.caret.set(null), 150);
  }
}
