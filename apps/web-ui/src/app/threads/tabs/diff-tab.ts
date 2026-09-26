import { ChangeDetectionStrategy, Component, ViewEncapsulation, computed, effect, inject, input, signal, untracked } from '@angular/core';
import type { ThreadDiff } from '@desk/protocol';
import { EmptyState } from '../../components/empty-state';
import { describeError } from '../../components/toast';
import { DeskBridge, DeskCallError } from '../../core/desk-bridge';

type State = { status: 'loading' } | { status: 'ready'; diff: ThreadDiff } | { status: 'none'; message: string } | { status: 'error'; message: string };

/** Each file status's letter, in a `Map` so that no status finds an `Object.prototype` member. */
const FILE_STATUS = new Map<string, string>([
  ['added', 'A'],
  ['modified', 'M'],
  ['deleted', 'D'],
  ['renamed', 'R'],
  ['copied', 'C'],
]);

/** A patch line's colour: file headers, hunks, additions and deletions. */
function lineClass(line: string): string | null {
  if (line.startsWith('+++') || line.startsWith('---')) return 'diff-meta';
  if (line.startsWith('+')) return 'diff-line-add';
  if (line.startsWith('-')) return 'diff-line-del';
  if (line.startsWith('@@')) return 'diff-hunk';
  return line.startsWith('diff ') ? 'diff-meta' : null;
}

/** The thread's branch against its base, with a coloured patch. Scratch threads have no diff (409). */
@Component({
  selector: 'div[deskDiffTab]',
  imports: [EmptyState],
  changeDetection: ChangeDetectionStrategy.OnPush,
  encapsulation: ViewEncapsulation.None,
  host: { style: 'display: contents' },
  template: `
    @switch (state().status) {
      @case ('loading') {
        <p class="muted tab-body">Loading the diff…</p>
      }
      @case ('none') {
        <div deskEmptyState title="No diff for this thread" body="This thread works in a scratch workspace, not on a git branch. See Files instead."></div>
      }
      @case ('error') {
        <div deskEmptyState title="Couldn't load the diff" [body]="message()"></div>
      }
      @case ('ready') {
        @if (diff(); as d) {
          <div class="tab-body diff-tab">
            <p class="small"><span class="mono">{{ d.branch }}</span> against <span class="mono">{{ d.base }}</span> · {{ d.files.length }} file{{ d.files.length === 1 ? '' : 's' }} changed. Desk never merges; merge the branch yourself when you're happy.</p>
            @if (d.files.length) {
              <table class="diff-files">
                <tbody>
                  @for (f of d.files; track f.path) {
                    <tr>
                      <td class="mono diff-status" [attr.title]="f.status">{{ fileStatus(f.status) }}</td>
                      <td class="mono grow">{{ f.path }}</td>
                      <td class="mono diff-add">{{ f.additions === null ? 'bin' : '+' + f.additions }}</td>
                      <td class="mono diff-del">{{ f.deletions === null ? '' : '−' + f.deletions }}</td>
                    </tr>
                  }
                </tbody>
              </table>
            } @else {
              <p class="muted">No changes yet.</p>
            }
            @if (d.patch) {
              <pre class="diff-patch" aria-label="Patch">@for (line of lines(); track $index) {<span [attr.class]="line.cls">{{ line.text }}</span>}</pre>
            }
          </div>
        }
      }
    }
  `,
})
export class DiffTab {
  readonly threadId = input.required<string>();
  /** Changes whenever the thread's events do: the diff is fetched again. */
  readonly version = input.required<string>();
  private readonly bridge = inject(DeskBridge);
  protected readonly state = signal<State>({ status: 'loading' });
  protected readonly diff = computed(() => {
    const s = this.state();
    return s.status === 'ready' ? s.diff : null;
  });
  protected readonly message = computed(() => {
    const s = this.state();
    return s.status === 'error' || s.status === 'none' ? s.message : null;
  });
  /** One span per patch line, each ending in its newline (the React span holds the line and a '\n'). */
  protected readonly lines = computed(() => (this.diff()?.patch ?? '').split('\n').map((line) => ({ text: `${line}\n`, cls: lineClass(line) })));

  constructor() {
    effect((onCleanup) => {
      const id = this.threadId();
      this.version();
      let live = true;
      onCleanup(() => {
        live = false;
      });
      untracked(() => {
        this.bridge.call('threads.diff', { id }).then(
          (diff) => {
            if (live) this.state.set({ status: 'ready', diff });
          },
          (err: unknown) => {
            if (!live) return;
            if (err instanceof DeskCallError && err.status === 409) this.state.set({ status: 'none', message: err.message });
            else this.state.set({ status: 'error', message: describeError(err).message });
          },
        );
      });
    });
  }

  protected fileStatus(status: string): string {
    return FILE_STATUS.get(status) ?? status;
  }
}
