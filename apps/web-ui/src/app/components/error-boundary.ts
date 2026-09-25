import { NgTemplateOutlet } from '@angular/common';
import {
  ChangeDetectionStrategy,
  ChangeDetectorRef,
  Component,
  computed,
  contentChild,
  DestroyRef,
  ErrorHandler,
  inject,
  Injectable,
  Injector,
  input,
  PendingTasks,
  signal,
  TemplateRef,
  untracked,
  ViewEncapsulation,
  type Provider,
} from '@angular/core';
import { Button } from './button';
import { EmptyState } from './empty-state';

const SCREEN_BODY = 'Other screens still work. Your projects and threads are safe; they live in deskd.';
const WHOLE_BODY = 'Reload to continue. Your projects and threads are safe; they live in deskd.';

/**
 * Catches a render error so one broken screen never blanks the page. Its content is a single `<ng-template>`, rendered while
 * healthy; give it the screen's key so navigating elsewhere starts clean. "Try again" renders it afresh, "Reload Desk" reloads.
 */
@Component({
  selector: 'div[deskErrorBoundary]',
  imports: [NgTemplateOutlet, Button, EmptyState],
  changeDetection: ChangeDetectionStrategy.OnPush,
  encapsulation: ViewEncapsulation.None,
  host: {
    '[class.error-boundary]': 'error() !== null',
    '[class.error-boundary-whole]': "error() !== null && whole()",
    '[attr.role]': "error() !== null ? 'alert' : null",
    '[style.display]': "error() !== null ? null : 'contents'",
  },
  template: `
    @if (error(); as e) {
      <div deskEmptyState [title]="whole() ? 'Desk hit an error' : 'This screen hit an error'" [body]="whole() ? wholeBody : screenBody">
        <div class="error-boundary-actions">
          @if (!whole()) {
            <button deskButton variant="primary" (click)="retry()">Try again</button>
          }
          <button deskButton [variant]="whole() ? 'primary' : 'secondary'" (click)="reload()">Reload Desk</button>
        </div>
      </div>
      <pre class="error-boundary-detail">{{ e.message }}</pre>
    } @else {
      <ng-container [ngTemplateOutlet]="content()" />
    }
  `,
})
export class ErrorBoundary {
  /** `whole` covers the entire page (there is no title bar left to navigate away with). */
  readonly scope = input<'screen' | 'whole'>('screen');
  readonly resetKey = input('');
  protected readonly content = contentChild.required(TemplateRef, { descendants: false });
  /** The failure and the screen key it happened under: a new key (the next screen) starts clean. */
  private readonly failure = signal<{ key: string; error: Error } | null>(null);
  protected readonly error = computed(() => {
    const f = this.failure();
    return f && f.key === this.resetKey() ? f.error : null;
  });
  protected readonly whole = computed(() => this.scope() === 'whole');
  protected readonly screenBody = SCREEN_BODY;
  protected readonly wholeBody = WHOLE_BODY;
  private readonly cdr = inject(ChangeDetectorRef);
  private readonly pendingTasks = inject(PendingTasks);
  private destroyed = false;

  constructor() {
    const unregister = inject(ErrorBoundaries).register(this);
    inject(DestroyRef).onDestroy(() => {
      this.destroyed = true;
      unregister();
    });
  }

  /** Whether it is showing an error (then it takes no more). */
  failed(): boolean {
    return untracked(this.error) !== null;
  }

  fail(error: unknown): void {
    this.failure.set({ key: untracked(this.resetKey), error: error instanceof Error ? error : new Error(String(error)) });
    // A render error reaches the ErrorHandler before the zoneless scheduler has finished its tick, and it schedules nothing
    // then: ask for the fallback's render once that tick is over, and keep the app unstable until it is asked for.
    const done = this.pendingTasks.add();
    queueMicrotask(() => {
      if (!this.destroyed) this.cdr.markForCheck();
      done();
    });
  }

  protected retry(): void {
    this.failure.set(null);
  }

  protected reload(): void {
    window.location.reload();
  }
}

/** The boundaries on the page, outermost first. */
@Injectable({ providedIn: 'root' })
export class ErrorBoundaries {
  private readonly stack: ErrorBoundary[] = [];

  register(boundary: ErrorBoundary): () => void {
    this.stack.push(boundary);
    return () => {
      const i = this.stack.indexOf(boundary);
      if (i >= 0) this.stack.splice(i, 1);
    };
  }

  /** Hands the error to the innermost boundary that is not already showing one; false when none took it. */
  report(error: unknown): boolean {
    for (let i = this.stack.length - 1; i >= 0; i--) {
      const boundary = this.stack[i]!;
      if (!boundary.failed()) {
        boundary.fail(error);
        return true;
      }
    }
    return false;
  }
}

/** The app's ErrorHandler: logs like the desktop's boundary, then lets the innermost boundary show the error. */
@Injectable()
export class DeskErrorHandler implements ErrorHandler {
  private readonly injector = inject(Injector);

  handleError(error: unknown): void {
    console.error('Desk screen error', error);
    this.injector.get(ErrorBoundaries).report(error);
  }
}

/** Installs DeskErrorHandler (app.config.ts, and specs that exercise a boundary). */
export function provideErrorBoundaries(): Provider[] {
  return [{ provide: ErrorHandler, useClass: DeskErrorHandler }];
}
