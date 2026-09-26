import { signal } from '@angular/core';
import { fireEvent, render, screen, within } from '@testing-library/angular';
import { describe, expect, it } from 'vitest';
import { DEFAULT_POLICY, type PolicyRule } from '@desk/protocol';
import { PolicyEditor } from './policy-editor';

/** A host that keeps the rules, as SettingsScreen does; returns the signal it binds. */
async function setup(initial: PolicyRule[]) {
  const rules = signal<PolicyRule[]>(initial);
  await render(`<div deskPolicyEditor [rules]="rules()" (changed)="rules.set($event)"></div>`, { imports: [PolicyEditor], componentProperties: { rules } });
  return rules;
}

const rule = (n: number) => screen.getByRole('listitem', { name: `Rule ${n}` });

describe('PolicyEditor', () => {
  it('lets Desk decide only for rules that ask, and moves and removes rules', async () => {
    const rules = await setup([
      { tool: 'bash', action: 'ask' },
      { tool: 'web_fetch', action: 'allow' },
    ]);
    const delegate = (n: number) => within(rule(n)).getByLabelText('Desk decides') as HTMLInputElement;
    expect(delegate(2).disabled).toBe(true);
    expect(delegate(2).closest('label')?.classList.contains('hidden')).toBe(true);
    expect(delegate(1).closest('label')?.classList.contains('hidden')).toBe(false);
    fireEvent.click(delegate(1));
    expect(rules()[0]).toEqual({ tool: 'bash', action: 'ask', delegate_to_desk: true });
    fireEvent.click(delegate(1));
    expect(rules()[0]).toEqual({ tool: 'bash', action: 'ask' });

    expect((screen.getByRole('button', { name: 'Move rule 1 up' }) as HTMLButtonElement).disabled).toBe(true);
    expect((screen.getByRole('button', { name: 'Move rule 2 down' }) as HTMLButtonElement).disabled).toBe(true);
    fireEvent.click(screen.getByRole('button', { name: 'Move rule 1 down' }));
    expect(rules().map((r) => r.tool)).toEqual(['web_fetch', 'bash']);
    expect((within(rule(1)).getByLabelText('Rule 1 tool') as HTMLInputElement).value).toBe('web_fetch');
    fireEvent.click(screen.getByRole('button', { name: 'Remove rule 1' }));
    expect(rules()).toEqual([{ tool: 'bash', action: 'ask' }]);
    expect(screen.queryByRole('listitem', { name: 'Rule 2' })).toBeNull();
  });

  it('names the built-in risky pattern until it is replaced, and resets to a copy of the default', async () => {
    const rules = await setup(DEFAULT_POLICY.map((r) => ({ ...r })));
    expect(screen.getAllByText('risky commands')).toHaveLength(4);
    expect(screen.getByText('This is the default policy.')).toBeTruthy();
    expect((screen.getByRole('button', { name: 'Reset to the default policy' }) as HTMLButtonElement).disabled).toBe(true);
    fireEvent.click(within(rule(1)).getByRole('button', { name: 'Replace' }));
    expect(rules()[0]).toEqual({ tool: 'bash', match: { command: '' }, action: 'ask' });
    const pattern = screen.getByLabelText('Rule 1 pattern') as HTMLInputElement;
    expect(pattern.value).toBe('');
    expect(pattern.placeholder).toBe('a regular expression');
    expect(screen.getAllByText('risky commands')).toHaveLength(3);
    expect(screen.queryByText('This is the default policy.')).toBeNull();
    fireEvent.click(screen.getByRole('button', { name: 'Reset to the default policy' }));
    expect(rules()).toEqual(DEFAULT_POLICY);
    expect(rules()[0]).not.toBe(DEFAULT_POLICY[0]);
    expect(rules()[0]!.match).not.toBe(DEFAULT_POLICY[0]!.match);
    expect(screen.getByText('This is the default policy.')).toBeTruthy();
  });

  it("keeps the pattern when the match kind changes, and 'any call' drops the match", async () => {
    const rules = await setup([{ tool: 'git_push', match: { branch: 'desk/*' }, action: 'allow' }]);
    const pattern = screen.getByLabelText('Rule 1 pattern') as HTMLInputElement;
    expect(pattern.value).toBe('desk/*');
    expect(pattern.placeholder).toBe('a glob such as desk/*');
    expect((screen.getByLabelText('Rule 1 match') as HTMLSelectElement).value).toBe('branch');
    fireEvent.change(screen.getByLabelText('Rule 1 match'), { target: { value: 'domain' } });
    expect(rules()[0]).toEqual({ tool: 'git_push', match: { domain: 'desk/*' }, action: 'allow' });
    expect((screen.getByLabelText('Rule 1 pattern') as HTMLInputElement).placeholder).toBe('a glob such as *.github.com');
    fireEvent.change(screen.getByLabelText('Rule 1 match'), { target: { value: 'none' } });
    expect(rules()[0]).toEqual({ tool: 'git_push', action: 'allow' });
    expect(screen.queryByLabelText('Rule 1 pattern')).toBeNull();
    fireEvent.input(screen.getByLabelText('Rule 1 tool'), { target: { value: 'open_pr' } });
    fireEvent.change(screen.getByLabelText('Rule 1 action'), { target: { value: 'ask' } });
    expect(rules()).toEqual([{ tool: 'open_pr', action: 'ask' }]);
    expect((screen.getByLabelText('Desk decides') as HTMLInputElement).disabled).toBe(false);
  });
});
