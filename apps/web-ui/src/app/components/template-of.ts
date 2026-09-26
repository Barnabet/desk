import { Directive, input } from '@angular/core';

/**
 * Types an `<ng-template>`'s `$implicit` for strict templates, which otherwise read `let-x` as `any`:
 * `<ng-template #entry let-e [deskTemplateOf]="entryType">` with `protected readonly entryType = templateOf<PairEntry>()`.
 * Only the input's type matters; nothing reads its value.
 */
@Directive({ selector: 'ng-template[deskTemplateOf]' })
export class TemplateOf<T> {
  readonly deskTemplateOf = input.required<T>();

  static ngTemplateContextGuard<T>(_dir: TemplateOf<T>, ctx: unknown): ctx is { $implicit: T } {
    return true;
  }
}

/** The type token `TemplateOf` reads: `templateOf<PairEntry>()`. */
export function templateOf<T>(): T {
  return undefined as T;
}
