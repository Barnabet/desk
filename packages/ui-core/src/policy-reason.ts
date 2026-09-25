import { RISKY_COMMAND_PATTERN } from '@desk/protocol';

/**
 * The daemon explains an approval with the raw rule ("Policy rule {…} → ask"). This turns it into
 * a sentence for people, plus a short chip that names the rule.
 */
export function policyReason(reason: string): { text: string; chip: string | null } {
  const m = /^Policy rule (\{.*\}) → (\w+)$/s.exec(reason.trim());
  if (!m) return { text: reason, chip: null };
  try {
    const rule = JSON.parse(m[1]!) as { tool: string; match?: Record<string, string> };
    const action = m[2]!;
    const chip = `${rule.tool}${rule.match ? ` · ${Object.keys(rule.match).join(', ')} matches` : ''} → ${action}`;
    if (rule.match?.command === RISKY_COMMAND_PATTERN)
      return { text: 'Your policy always asks before a risky command, such as sudo, piping a download into a shell, or a recursive delete, even inside the sandbox.', chip: `${rule.tool} · risky command → ${action}` };
    if (rule.match) {
      const what = Object.entries(rule.match)
        .map(([k, v]) => `${k} matching ${v}`)
        .join(' and ');
      return { text: `A policy rule asks before ${rule.tool} runs with ${what}.`, chip };
    }
    return { text: `Your policy asks before every ${rule.tool} call.`, chip };
  } catch {
    return { text: reason, chip: null };
  }
}
