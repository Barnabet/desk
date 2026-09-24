import { describe, expect, it } from 'vitest';
import { RISKY_COMMAND_PATTERN } from '@desk/protocol';
import { policyReason } from './policyReason';

describe('policyReason', () => {
  it('explains the risky-command rule in words', () => {
    const r = policyReason(`Policy rule ${JSON.stringify({ tool: 'bash', match: { command: RISKY_COMMAND_PATTERN } })} → ask`);
    expect(r.text).toMatch(/^Your policy always asks before a risky command/);
    expect(r.chip).toBe('bash · risky command → ask');
  });

  it('names other rules and passes plain reasons through', () => {
    expect(policyReason('Policy rule {"tool":"web_fetch"} → ask')).toEqual({ text: 'Your policy asks before every web_fetch call.', chip: 'web_fetch → ask' });
    expect(policyReason('Policy rule {"tool":"git_push","match":{"branch":"main"}} → ask').text).toBe('A policy rule asks before git_push runs with branch matching main.');
    expect(policyReason('No policy rule allows bash')).toEqual({ text: 'No policy rule allows bash', chip: null });
  });
});
