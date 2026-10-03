import type { PolicyRule } from '@desk/protocol';
import type { PolicySubject, Tool } from '../tools/types';

export type PolicyDecision = {
  action: 'auto' | 'allow' | 'ask' | 'deny';
  rule?: PolicyRule;
  delegateToDesk: boolean;
  reason: string;
  /** A denial's tool result, instead of "Denied by policy. <reason>" (an answer run's own rules). */
  denial?: string;
};

const SHELL_TOOLS = new Set(['bash', 'bash_background', 'bash_readonly', 'skill_run', 'service_start', 'run_check']);
/** Strictest first: a call with several subjects gets the strictest of their decisions. */
const STRICTNESS = ['deny', 'ask', 'allow', 'auto'] as const;

export function globToRegExp(glob: string): RegExp {
  const body = glob.replace(/[.+^${}()|[\]\\]/g, '\\$&').replace(/\*/g, '.*').replace(/\?/g, '.');
  return new RegExp(`^${body}$`);
}

function safeRegExp(source: string): RegExp | null {
  try {
    return new RegExp(source);
  } catch {
    return null;
  }
}

function ruleMatches(rule: PolicyRule, toolName: string, subject: PolicySubject): boolean {
  if (rule.tool !== '*' && rule.tool !== toolName) return false;
  const m = rule.match;
  if (!m) return true;
  if (m.branch !== undefined && (subject.branch === undefined || !globToRegExp(m.branch).test(subject.branch))) return false;
  if (m.domain !== undefined && (subject.domain === undefined || !globToRegExp(m.domain).test(subject.domain))) return false;
  if (m.command !== undefined) {
    const re = safeRegExp(m.command);
    if (!re || subject.command === undefined || !re.test(subject.command)) return false;
  }
  return true;
}

/** Decides whether a validated tool call may run. First matching rule wins, for each subject of the call. */
export function evaluatePolicy(
  tool: Tool,
  input: unknown,
  rules: PolicyRule[],
  opts: { sandboxAvailable: boolean; gitBranch?: string },
): PolicyDecision {
  if (!tool.gate) return { action: 'auto', delegateToDesk: false, reason: 'Tool is not policy-gated' };

  const gctx = opts.gitBranch ? { gitBranch: opts.gitBranch } : {};
  let subjects: PolicySubject[];
  try {
    subjects = tool.gate.subjects ? tool.gate.subjects(input, gctx) : [tool.gate.subject(input, gctx)];
  } catch {
    subjects = [{}];
  }
  const decisions = (subjects.length ? subjects : [{}]).map((s) => decide(tool, s, rules, opts));
  return decisions.reduce((a, b) => (STRICTNESS.indexOf(b.action) < STRICTNESS.indexOf(a.action) ? b : a));
}

function decide(tool: Tool, subject: PolicySubject, rules: PolicyRule[], opts: { sandboxAvailable: boolean }): PolicyDecision {
  const gate = tool.gate!;
  const names = [tool.name, ...(gate.alsoMatches ?? [])];
  const rule = rules.find((r) => names.some((n) => ruleMatches(r, n, subject)));
  if (rule) {
    return {
      action: rule.action,
      rule,
      delegateToDesk: rule.delegate_to_desk ?? false,
      reason: `Policy rule ${JSON.stringify({ tool: rule.tool, ...(rule.match ? { match: rule.match } : {}) })} → ${rule.action}`,
    };
  }

  if (SHELL_TOOLS.has(tool.name) && !opts.sandboxAvailable) {
    return { action: 'ask', delegateToDesk: false, reason: 'Shell sandbox unavailable: every command needs approval' };
  }
  return {
    action: gate.unmatched,
    delegateToDesk: false,
    reason: gate.unmatched === 'ask' ? `No policy rule allows ${tool.name}` : 'No policy rule matched',
  };
}
