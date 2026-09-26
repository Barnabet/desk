import { ChangeDetectionStrategy, Component, ViewEncapsulation, computed, input, output } from '@angular/core';
import { DEFAULT_POLICY, RISKY_COMMAND_PATTERN, type PolicyRule } from '@desk/protocol';
import { Button } from '../components/button';

type MatchKey = 'branch' | 'command' | 'domain';
const TOOLS = ['bash', 'bash_background', 'bash_readonly', 'skill_run', 'git_push', 'open_pr', 'web_fetch', 'web_search'];
const MATCH_HINT: Record<MatchKey, string> = { branch: 'a glob such as desk/*', command: 'a regular expression', domain: 'a glob such as *.github.com' };

/** The same rules in the same order. */
export const sameRules = (a: PolicyRule[], b: PolicyRule[]): boolean => JSON.stringify(a) === JSON.stringify(b);

function matchOf(r: PolicyRule): [MatchKey | 'none', string] {
  const m = r.match ?? {};
  for (const k of ['branch', 'command', 'domain'] as const) if (m[k] !== undefined) return [k, m[k]!];
  return ['none', ''];
}

function withMatch(r: PolicyRule, key: MatchKey | 'none', pattern: string): PolicyRule {
  const { match: _m, ...rest } = r;
  return key === 'none' ? rest : { ...rest, match: { [key]: pattern } };
}

/** One row as the template shows it. */
type RuleView = { rule: PolicyRule; key: MatchKey | 'none'; pattern: string; risky: boolean };

/** The ordered policy: the first rule that matches a tool call decides whether it runs, asks or is refused (PolicyEditor.tsx). */
@Component({
  selector: 'div[deskPolicyEditor]',
  imports: [Button],
  changeDetection: ChangeDetectionStrategy.OnPush,
  encapsulation: ViewEncapsulation.None,
  host: { class: 'policy' },
  template: `
    <datalist id="policy-tools">
      @for (t of tools; track t) {
        <option [value]="t"></option>
      }
    </datalist>
    <p class="field-hint">Rules are checked top to bottom; the first match decides. Tools no rule matches use their built-in default. Shell tools always run in the sandbox.</p>
    <ol class="policy-rules">
      @for (v of views(); track $index; let i = $index, last = $last) {
        <li class="policy-rule" [attr.aria-label]="'Rule ' + (i + 1)">
          <span class="policy-num" aria-hidden="true">{{ i + 1 }}</span>
          <input class="input" list="policy-tools" [attr.aria-label]="'Rule ' + (i + 1) + ' tool'" [value]="v.rule.tool" (input)="setTool(i, $event)" />
          <select class="select" [attr.aria-label]="'Rule ' + (i + 1) + ' match'" [value]="v.key" (change)="setMatchKey(i, $event)">
            <option value="none">any call</option>
            <option value="branch">branch</option>
            <option value="command">command</option>
            <option value="domain">domain</option>
          </select>
          @if (v.key === 'none') {
            <span></span>
          } @else if (v.risky) {
            <span class="policy-risky"><span class="chip chip-idle" title="The built-in pattern: sudo, piping a download into a shell, recursive deletes of / or ~, and similar">risky commands</span><button type="button" class="link small" (click)="setPattern(i, '')">Replace</button></span>
          } @else {
            <input class="input mono" [attr.aria-label]="'Rule ' + (i + 1) + ' pattern'" [placeholder]="hint(v.key)" [value]="v.pattern" (input)="setPattern(i, val($event))" />
          }
          <select class="select" [attr.aria-label]="'Rule ' + (i + 1) + ' action'" [value]="v.rule.action" (change)="setAction(i, $event)">
            <option value="allow">allow</option>
            <option value="ask">ask</option>
            <option value="deny">deny</option>
          </select>
          <label class="policy-delegate small" [class.hidden]="v.rule.action !== 'ask'"><input type="checkbox" [checked]="v.rule.delegate_to_desk ?? false" [disabled]="v.rule.action !== 'ask'" (change)="setDelegate(i, $event)" />&ngsp;Desk decides</label>
          <span class="policy-row-actions">
            <button type="button" class="icon-btn" [attr.aria-label]="'Move rule ' + (i + 1) + ' up'" [disabled]="i === 0" (click)="move(i, -1)">↑</button>
            <button type="button" class="icon-btn" [attr.aria-label]="'Move rule ' + (i + 1) + ' down'" [disabled]="last" (click)="move(i, 1)">↓</button>
            <button type="button" class="icon-btn" [attr.aria-label]="'Remove rule ' + (i + 1)" (click)="remove(i)">✕</button>
          </span>
        </li>
      }
    </ol>
    <div class="actions">
      <button deskButton size="sm" (click)="add()">Add rule</button>
      <button deskButton size="sm" variant="ghost" [disabled]="isDefault()" (click)="reset()">Reset to the default policy</button>
      @if (isDefault()) {
        <span class="muted small">This is the default policy.</span>
      }
    </div>
  `,
})
export class PolicyEditor {
  readonly rules = input.required<PolicyRule[]>();
  /** The next list of rules (React's `onChange`); the host keeps it and passes it back as `rules`. */
  readonly changed = output<PolicyRule[]>();

  protected readonly tools = TOOLS;
  protected readonly views = computed<RuleView[]>(() =>
    this.rules().map((rule) => {
      const [key, pattern] = matchOf(rule);
      return { rule, key, pattern, risky: key === 'command' && pattern === RISKY_COMMAND_PATTERN };
    }),
  );
  protected readonly isDefault = computed(() => sameRules(this.rules(), DEFAULT_POLICY));

  protected val(e: Event): string {
    return (e.target as HTMLInputElement).value;
  }

  protected hint(key: MatchKey | 'none'): string {
    return key === 'none' ? '' : MATCH_HINT[key];
  }

  protected setTool(i: number, e: Event): void {
    this.set(i, { ...this.at(i), tool: (e.target as HTMLInputElement).value });
  }

  /** A new match kind keeps the pattern typed so far. */
  protected setMatchKey(i: number, e: Event): void {
    const r = this.at(i);
    this.set(i, withMatch(r, (e.target as HTMLSelectElement).value as MatchKey | 'none', matchOf(r)[1]));
  }

  protected setPattern(i: number, pattern: string): void {
    const r = this.at(i);
    this.set(i, withMatch(r, matchOf(r)[0], pattern));
  }

  protected setAction(i: number, e: Event): void {
    this.set(i, { ...this.at(i), action: (e.target as HTMLSelectElement).value as PolicyRule['action'] });
  }

  protected setDelegate(i: number, e: Event): void {
    const { delegate_to_desk: _d, ...rest } = this.at(i);
    this.set(i, (e.target as HTMLInputElement).checked ? { ...rest, delegate_to_desk: true } : rest);
  }

  protected move(i: number, d: number): void {
    const next = [...this.rules()];
    const [r] = next.splice(i, 1);
    next.splice(i + d, 0, r!);
    this.changed.emit(next);
  }

  protected remove(i: number): void {
    this.changed.emit(this.rules().filter((_, k) => k !== i));
  }

  protected add(): void {
    this.changed.emit([...this.rules(), { tool: 'bash', action: 'ask' }]);
  }

  /** A copy of the default, so later edits never touch DEFAULT_POLICY itself. */
  protected reset(): void {
    this.changed.emit(DEFAULT_POLICY.map((r) => ({ ...r, ...(r.match ? { match: { ...r.match } } : {}) })));
  }

  private at(i: number): PolicyRule {
    return this.rules()[i]!;
  }

  private set(i: number, r: PolicyRule): void {
    this.changed.emit(this.rules().map((x, k) => (k === i ? r : x)));
  }
}
