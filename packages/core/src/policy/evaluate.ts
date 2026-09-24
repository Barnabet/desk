import type { PolicyRule } from '@desk/protocol';
import type { PolicySubject, Tool } from '../tools/types';

export type PolicyDecision = {
  action: 'auto' | 'allow' | 'ask' | 'deny';
  rule?: PolicyRule;
  delegateToDesk: boolean;
  reason: string;
};

const SHELL_TOOLS = new Set(['bash', 'bash_background', 'bash_readonly', 'skill_run', 'service_start']);

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

/** Decides whether a validated tool call may run. First matching rule wins. */
export function evaluatePolicy(
  tool: Tool,
  input: unknown,
  rules: PolicyRule[],
  opts: { sandboxAvailable: boolean; gitBranch?: string },
): PolicyDecision {
  if (!tool.gate) return { action: 'auto', delegateToDesk: false, reason: 'Tool is not policy-gated' };

  let subject: PolicySubject;
  try {
    subject = tool.gate.subject(input, opts.gitBranch ? { gitBranch: opts.gitBranch } : {});
  } catch {
    subject = {};
  }

  const names = [tool.name, ...(tool.gate.alsoMatches ?? [])];
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
    action: tool.gate.unmatched,
    delegateToDesk: false,
    reason: tool.gate.unmatched === 'ask' ? `No policy rule allows ${tool.name}` : 'No policy rule matched',
  };
}
