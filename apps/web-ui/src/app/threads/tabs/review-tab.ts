import { NgTemplateOutlet } from '@angular/common';
import { ChangeDetectionStrategy, Component, ViewEncapsulation, computed, effect, inject, input, signal, untracked } from '@angular/core';
import type { ThreadReview, ThreadView } from '@desk/client';
import { clock, criteriaDraft, href, linesOf, reviewView, shortCommit, since, type FindingView } from '@desk/ui-core';
import { Button } from '../../components/button';
import { EmptyState } from '../../components/empty-state';
import { describeError, ToastService } from '../../components/toast';
import { DeskBridge } from '../../core/desk-bridge';

type State = { status: 'loading' } | { status: 'ready'; review: ThreadReview } | { status: 'error'; message: string };
type Mode = 'review' | 'accept' | 'limits' | 'changes';

const TITLES: Record<Mode, string> = {
  review: 'Request a review',
  accept: 'Accept this submission',
  limits: 'Accept with limitations',
  changes: 'Request changes',
};

/**
 * A thread's submissions, reviews and findings, and the user's decisions about them (reviews and acceptance spec §7):
 * request a review, accept (with limitations) or request changes, and waive a finding. A phone only reads them.
 */
@Component({
  selector: 'div[deskReviewTab]',
  imports: [NgTemplateOutlet, Button, EmptyState],
  changeDetection: ChangeDetectionStrategy.OnPush,
  encapsulation: ViewEncapsulation.None,
  host: { style: 'display: contents' },
  template: `
    <ng-template #finding let-f>
      <li class="review-item">
        <div class="review-item-head">
          <strong>{{ f.title }}</strong>
          @if (f.blocking) {
            <span class="chip chip-fail">Blocking</span>
          }
          @if (f.outcome) {
            <span class="small muted">{{ f.outcome }}</span>
          }
          @if (canAct() && f.open && waiving() !== f.id) {
            <button deskButton size="sm" variant="ghost" (click)="startWaive(f.id)">Waive</button>
          }
        </div>
        @if (f.detail) {
          <p class="review-text">{{ f.detail }}</p>
        }
        @if (f.reproducer) {
          <pre class="review-repro" aria-label="Reproducer">{{ f.reproducer }}</pre>
        }
        @if (f.raisedOn) {
          <span class="small muted">Raised on submission {{ f.raisedOn }}</span>
        }
        @if (canAct() && f.open && waiving() === f.id) {
          <div class="actions">
            <input class="input" aria-label="Why waive it" placeholder="Why it can stay (optional)" [value]="waiveReason()" (input)="waiveReason.set(value($event))" />
            <button deskButton size="sm" [pending]="busy() === f.id" (click)="waive(f)">Waive finding</button>
            <button deskButton size="sm" variant="ghost" (click)="waiving.set(null)">Cancel</button>
          </div>
        }
      </li>
    </ng-template>

    @switch (state().status) {
      @case ('loading') {
        <p class="muted tab-body">Loading the review…</p>
      }
      @case ('error') {
        <div deskEmptyState title="Couldn't load the review" [body]="message()"></div>
      }
      @case ('ready') {
        @if (view(); as v) {
          @if (v.reviewing; as r) {
            <div class="tab-body review-tab">
              <p>This thread reviews {{ r.submissionSeq ? 'submission ' + r.submissionSeq + ' of ' : '' }}<a [href]="link(r.builderId)">{{ name(r.builderId) }}</a>{{ r.filed ? ', and has filed its review.' : '.' }} Its findings and verdict are on that thread's Review tab.</p>
            </div>
          } @else if (!v.current) {
            <div deskEmptyState title="Nothing submitted yet" body="A thread submits its work when it finishes: its commit, the files it published and what it says it met."></div>
          } @else {
            @let current = v.current;
            <div class="tab-body review-tab">
              <p class="review-state">
                @if (v.chip; as chip) {
                  <span [class]="'chip chip-' + chip.tone">{{ chip.label }}</span>
                } @else {
                  <span class="chip chip-idle">Not reviewed</span>
                }
                <span>Submission {{ current.seq }}@if (current.commit; as commit) {&ngsp;· commit <span class="mono">{{ short(commit) }}</span>}{{ current.artifacts.length ? ' · ' + current.artifacts.length + ' file' + (current.artifacts.length === 1 ? '' : 's') : '' }} · {{ ago(current.createdAt) }}</span>
              </p>
              @if (bridge.remote) {
                <p class="small muted">Accept, request changes or a review on your Mac.</p>
              } @else if (!archived() && !mode()) {
                <div class="actions">
                  <button deskButton size="sm" (click)="open('review')">{{ v.reReviewer ? 'Request a re-review' : 'Request a review' }}</button>
                  <button deskButton size="sm" (click)="open('accept')">Accept</button>
                  <button deskButton size="sm" (click)="open('limits')">Accept with limitations</button>
                  <button deskButton size="sm" (click)="open('changes')">Request changes</button>
                </div>
              }
              @if (mode(); as m) {
                <div class="review-form" role="group" [attr.aria-label]="titles[m]">
                  <strong>{{ titles[m] }}</strong>
                  @switch (m) {
                    @case ('review') {
                      <div class="field">
                        <label for="review-criteria">Acceptance criteria, one per line</label>
                        <textarea id="review-criteria" class="textarea" [value]="text()" (input)="text.set(value($event))"></textarea>
                        <p class="field-hint">A reviewer thread checks the submitted work against these without seeing the thread's own report first.</p>
                      </div>
                      <div class="field">
                        <label for="review-focus">Focus (optional)</label>
                        <input id="review-focus" class="input" [value]="focus()" (input)="focus.set(value($event))" />
                      </div>
                      @if (v.reReviewer; as rr) {
                        <label class="small"><input type="checkbox" [checked]="same()" (change)="same.set(checked($event))" /> Ask {{ name(rr) }} again</label>
                      }
                    }
                    @case ('limits') {
                      <div class="field">
                        <label for="review-limits">Limitations, one per line</label>
                        <textarea id="review-limits" class="textarea" [value]="text()" (input)="text.set(value($event))"></textarea>
                      </div>
                    }
                    @default {
                      <div class="field">
                        <label for="review-note">{{ m === 'changes' ? 'What needs to change' : 'Note (optional)' }}</label>
                        <textarea id="review-note" class="textarea" [value]="text()" (input)="text.set(value($event))"></textarea>
                        @if (m === 'changes' && v.open.length) {
                          <p class="field-hint">The open findings go to the thread with your note.</p>
                        }
                      </div>
                    }
                  }
                  @if (blocked()) {
                    <p class="field-error">Waive the {{ v.blocking }} open blocking finding{{ v.blocking === 1 ? '' : 's' }} below first, or request changes.</p>
                  }
                  @if (error(); as e) {
                    <p class="field-error">{{ e }}</p>
                  }
                  <div class="actions">
                    <button deskButton size="sm" variant="primary" [pending]="busy() === m" [disabled]="blocked()" (click)="submit()">{{ titles[m] }}</button>
                    <button deskButton size="sm" variant="ghost" (click)="mode.set(null)">Cancel</button>
                  </div>
                </div>
              }

              <h3>Open findings ({{ v.open.length }})</h3>
              @if (v.open.length) {
                <ul class="review-list">
                  @for (f of v.open; track f.id) {
                    <ng-container *ngTemplateOutlet="finding; context: { $implicit: f }" />
                  }
                </ul>
              } @else {
                <p class="muted">None.</p>
              }

              <h3>Reviews</h3>
              @if (v.reviews.length) {
                <ul class="review-list">
                  @for (r of v.reviews; track r.id) {
                    <li class="review-item">
                      <div class="review-item-head">
                        <strong><a [href]="link(r.reviewerId)">{{ name(r.reviewerId) }}</a>{{ r.submissionSeq ? ' · submission ' + r.submissionSeq : '' }}</strong>
                        <span [class]="'chip chip-' + r.chip.tone">{{ r.chip.label }}</span>
                        <span class="small muted">Requested by {{ r.requestedBy === 'user' ? 'you' : 'Desk' }}</span>
                      </div>
                      <ul class="review-req">
                        @if (r.requirements.length) {
                          @for (q of r.requirements; track $index) {
                            <li><span [class]="q.ok === true ? 'met-yes' : q.ok === false ? 'met-no' : 'muted'">{{ q.met }}</span>: {{ q.criterion }}@if (q.note) {<span class="muted"> · {{ q.note }}</span>}</li>
                          }
                        } @else {
                          @for (c of r.criteria; track $index) {
                            <li>{{ c }}</li>
                          }
                        }
                      </ul>
                      @if (r.notChecked.length) {
                        <span class="small muted">Not checked: {{ r.notChecked.join('; ') }}</span>
                      }
                    </li>
                  }
                </ul>
              } @else {
                <p class="muted">No review yet.</p>
              }

              <h3>Submissions</h3>
              <ul class="review-list">
                @for (sub of submissions(); track sub.id) {
                  <li class="review-item">
                    <div class="review-item-head">
                      <strong>Submission {{ sub.seq }}@if (sub.commit; as commit) {&ngsp;· <span class="mono">{{ short(commit) }}</span>}</strong>
                      @if (sub.tag; as tag) {
                        <span [class]="tag === 'Superseded' ? 'chip chip-idle' : 'chip chip-done'">{{ tag }}</span>
                      }
                      <span class="small muted">{{ time(sub.createdAt) }}</span>
                    </div>
                    @if (sub.claims.length) {
                      <span class="small">It says it met:</span>
                      <ul class="review-req">
                        @for (c of sub.claims; track $index) {
                          <li>{{ c }}</li>
                        }
                      </ul>
                    }
                    @if (sub.limitations.length) {
                      <span class="small">Limitations it reported:</span>
                      <ul class="review-req">
                        @for (c of sub.limitations; track $index) {
                          <li>{{ c }}</li>
                        }
                      </ul>
                    }
                    @if (sub.evidence) {
                      <p class="review-text small">How it checked: {{ sub.evidence }}</p>
                    }
                    @if (sub.artifacts.length) {
                      <span class="small mono">{{ paths(sub.artifacts) }}</span>
                    }
                  </li>
                }
              </ul>

              @if (v.resolved.length) {
                <h3>Resolved findings ({{ v.resolved.length }})</h3>
                <ul class="review-list">
                  @for (f of v.resolved; track f.id) {
                    <ng-container *ngTemplateOutlet="finding; context: { $implicit: f }" />
                  }
                </ul>
              }
            </div>
          }
        }
      }
    }
  `,
})
export class ReviewTab {
  readonly thread = input.required<ThreadView>();
  readonly threads = input.required<readonly ThreadView[]>();
  /** Changes whenever the thread's or any review event does: the review is fetched again. */
  readonly version = input.required<string>();
  readonly now = input.required<number>();

  protected readonly bridge = inject(DeskBridge);
  private readonly toasts = inject(ToastService);
  protected readonly titles = TITLES;
  protected readonly state = signal<State>({ status: 'loading' });
  protected readonly mode = signal<Mode | null>(null);
  protected readonly text = signal('');
  protected readonly focus = signal('');
  protected readonly same = signal(true);
  protected readonly busy = signal<string | null>(null);
  protected readonly error = signal<string | null>(null);
  protected readonly waiving = signal<string | null>(null);
  protected readonly waiveReason = signal('');
  private readonly tick = signal(0);
  private seenThread: string | null = null;

  protected readonly view = computed(() => {
    const s = this.state();
    return s.status === 'ready' ? reviewView(s.review) : null;
  });
  protected readonly message = computed(() => {
    const s = this.state();
    return s.status === 'error' ? s.message : null;
  });
  protected readonly archived = computed(() => !!this.thread().archived_at);
  protected readonly canAct = computed(() => !this.bridge.remote && !this.archived());
  protected readonly submissions = computed(() => {
    const v = this.view();
    return v?.current ? [v.current, ...v.earlier] : [];
  });
  protected readonly blocked = computed(() => {
    const m = this.mode();
    return (this.view()?.blocking ?? 0) > 0 && (m === 'accept' || m === 'limits');
  });

  constructor() {
    effect((onCleanup) => {
      const id = this.thread().id;
      this.version();
      this.tick();
      let live = true;
      onCleanup(() => {
        live = false;
      });
      untracked(() => {
        if (id !== this.seenThread) {
          this.seenThread = id;
          this.mode.set(null);
          this.waiving.set(null);
        }
        this.bridge.call('threads.review', { id }).then(
          (review) => {
            if (live) this.state.set({ status: 'ready', review });
          },
          (err: unknown) => {
            if (live) this.state.set({ status: 'error', message: describeError(err).message });
          },
        );
      });
    });
  }

  protected name(id: string): string {
    return this.threads().find((t) => t.id === id)?.title ?? 'a thread';
  }

  protected link(id: string): string {
    return href({ name: 'project', id: this.thread().project_id, tab: 'threads', threadId: id });
  }

  protected short(commit: string): string {
    return shortCommit(commit);
  }

  protected ago(iso: string): string {
    return since(iso, this.now());
  }

  protected time(iso: string): string {
    return clock(iso);
  }

  protected paths(artifacts: ReadonlyArray<{ path: string }>): string {
    return artifacts.map((a) => a.path).join(' · ');
  }

  protected value(e: Event): string {
    return (e.target as HTMLInputElement | HTMLTextAreaElement).value;
  }

  protected checked(e: Event): boolean {
    return (e.target as HTMLInputElement).checked;
  }

  protected open(m: Mode): void {
    const v = this.view();
    this.mode.set(m);
    this.error.set(null);
    this.focus.set('');
    this.same.set(true);
    this.text.set(m === 'review' && v ? criteriaDraft(v) : '');
  }

  protected startWaive(id: string): void {
    this.waiving.set(id);
    this.waiveReason.set('');
  }

  private done(message: string): void {
    this.mode.set(null);
    this.waiving.set(null);
    this.tick.update((n) => n + 1);
    this.toasts.toast({ tone: 'info', message });
  }

  protected async submit(): Promise<void> {
    const m = this.mode();
    const v = this.view();
    if (!m || !v?.current) return;
    const id = this.thread().id;
    const text = this.text();
    this.busy.set(m);
    this.error.set(null);
    try {
      if (m === 'review') {
        const criteria = linesOf(text);
        if (!criteria.length) throw new Error('Write at least one acceptance criterion.');
        const focus = this.focus().trim();
        const out = await this.bridge.call('threads.requestReview', {
          id,
          req: { criteria, ...(focus ? { focus } : {}), ...(v.reReviewer && this.same() ? { reviewer_id: v.reReviewer } : {}) },
        });
        this.done(out.reopened ? 'Asked the same reviewer to look again.' : 'A reviewer thread has started.');
      } else {
        const limitations = m === 'limits' ? linesOf(text) : [];
        if (m === 'limits' && !limitations.length) throw new Error('Write at least one limitation.');
        const decision = m === 'accept' ? 'accepted' : m === 'limits' ? 'accepted_with_limitations' : 'changes_requested';
        await this.bridge.call('threads.accept', { id, req: { decision, ...(limitations.length ? { limitations } : {}), ...(m !== 'limits' && text.trim() ? { note: text.trim() } : {}) } });
        this.done(m === 'changes' ? 'Sent the changes back to the thread.' : `Accepted submission ${v.current.seq}.`);
      }
    } catch (err) {
      this.error.set(describeError(err).message);
    } finally {
      this.busy.set(null);
    }
  }

  protected async waive(f: FindingView): Promise<void> {
    const reason = this.waiveReason().trim();
    this.busy.set(f.id);
    try {
      await this.bridge.call('threads.waiveFinding', { findingId: f.id, req: reason ? { reason } : {} });
      this.done('Finding waived.');
    } catch (err) {
      this.toasts.error(err);
    } finally {
      this.busy.set(null);
    }
  }
}
