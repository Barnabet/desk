import { DEFAULT_POLICY, RISKY_COMMAND_PATTERN, type PolicyRule } from '@desk/protocol';
import { Button } from '../components/Button';

type MatchKey = 'branch' | 'command' | 'domain';
const TOOLS = ['bash', 'bash_background', 'bash_readonly', 'skill_run', 'git_push', 'open_pr', 'web_fetch', 'web_search'];
const MATCH_HINT: Record<MatchKey, string> = { branch: 'a glob such as desk/*', command: 'a regular expression', domain: 'a glob such as *.github.com' };

export const sameRules = (a: PolicyRule[], b: PolicyRule[]) => JSON.stringify(a) === JSON.stringify(b);

function matchOf(r: PolicyRule): [MatchKey | 'none', string] {
  const m = r.match ?? {};
  for (const k of ['branch', 'command', 'domain'] as const) if (m[k] !== undefined) return [k, m[k]!];
  return ['none', ''];
}

function withMatch(r: PolicyRule, key: MatchKey | 'none', pattern: string): PolicyRule {
  const { match: _m, ...rest } = r;
  return key === 'none' ? rest : { ...rest, match: { [key]: pattern } };
}

/** The ordered policy: the first rule that matches a tool call decides whether it runs, asks or is refused. */
export function PolicyEditor({ rules, onChange }: { rules: PolicyRule[]; onChange(rules: PolicyRule[]): void }) {
  const set = (i: number, r: PolicyRule) => onChange(rules.map((x, k) => (k === i ? r : x)));
  const move = (i: number, d: -1 | 1) => {
    const next = [...rules];
    const [r] = next.splice(i, 1);
    next.splice(i + d, 0, r!);
    onChange(next);
  };
  return (
    <div className="policy">
      <datalist id="policy-tools">
        {TOOLS.map((t) => (
          <option key={t} value={t} />
        ))}
      </datalist>
      <p className="field-hint">Rules are checked top to bottom; the first match decides. Tools no rule matches use their built-in default. Shell tools always run in the sandbox.</p>
      <ol className="policy-rules">
        {rules.map((r, i) => {
          const [key, pattern] = matchOf(r);
          const risky = key === 'command' && pattern === RISKY_COMMAND_PATTERN;
          return (
            <li key={i} className="policy-rule" aria-label={`Rule ${i + 1}`}>
              <span className="policy-num" aria-hidden="true">
                {i + 1}
              </span>
              <input className="input" list="policy-tools" aria-label={`Rule ${i + 1} tool`} value={r.tool} onChange={(e) => set(i, { ...r, tool: e.target.value })} />
              <select className="select" aria-label={`Rule ${i + 1} match`} value={key} onChange={(e) => set(i, withMatch(r, e.target.value as MatchKey | 'none', pattern))}>
                <option value="none">any call</option>
                <option value="branch">branch</option>
                <option value="command">command</option>
                <option value="domain">domain</option>
              </select>
              {key === 'none' ? (
                <span />
              ) : risky ? (
                <span className="policy-risky">
                  <span className="chip chip-idle" title="The built-in pattern: sudo, piping a download into a shell, recursive deletes of / or ~, and similar">risky commands</span>
                  <button type="button" className="link small" onClick={() => set(i, withMatch(r, key, ''))}>
                    Replace
                  </button>
                </span>
              ) : (
                <input className="input mono" aria-label={`Rule ${i + 1} pattern`} placeholder={MATCH_HINT[key]} value={pattern} onChange={(e) => set(i, withMatch(r, key, e.target.value))} />
              )}
              <select className="select" aria-label={`Rule ${i + 1} action`} value={r.action} onChange={(e) => set(i, { ...r, action: e.target.value as PolicyRule['action'] })}>
                <option value="allow">allow</option>
                <option value="ask">ask</option>
                <option value="deny">deny</option>
              </select>
              <label className={`policy-delegate small${r.action === 'ask' ? '' : ' hidden'}`}>
                <input
                  type="checkbox"
                  checked={r.delegate_to_desk ?? false}
                  disabled={r.action !== 'ask'}
                  onChange={(e) => {
                    const { delegate_to_desk: _d, ...rest } = r;
                    set(i, e.target.checked ? { ...rest, delegate_to_desk: true } : rest);
                  }}
                />{' '}
                Desk decides
              </label>
              <span className="policy-row-actions">
                <button type="button" className="icon-btn" aria-label={`Move rule ${i + 1} up`} disabled={i === 0} onClick={() => move(i, -1)}>
                  ↑
                </button>
                <button type="button" className="icon-btn" aria-label={`Move rule ${i + 1} down`} disabled={i === rules.length - 1} onClick={() => move(i, 1)}>
                  ↓
                </button>
                <button type="button" className="icon-btn" aria-label={`Remove rule ${i + 1}`} onClick={() => onChange(rules.filter((_, k) => k !== i))}>
                  ✕
                </button>
              </span>
            </li>
          );
        })}
      </ol>
      <div className="actions">
        <Button size="sm" onClick={() => onChange([...rules, { tool: 'bash', action: 'ask' }])}>
          Add rule
        </Button>
        <Button size="sm" variant="ghost" disabled={sameRules(rules, DEFAULT_POLICY)} onClick={() => onChange(DEFAULT_POLICY.map((r) => ({ ...r, ...(r.match ? { match: { ...r.match } } : {}) })))}>
          Reset to the default policy
        </Button>
        {sameRules(rules, DEFAULT_POLICY) ? <span className="muted small">This is the default policy.</span> : null}
      </div>
    </div>
  );
}
