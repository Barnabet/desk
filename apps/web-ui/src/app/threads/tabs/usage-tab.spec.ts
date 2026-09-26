import { render, screen } from '@testing-library/angular';
import { describe, expect, it } from 'vitest';
import { ev } from '@desk/client/testing';
import { FakeDeskBridge } from '../../testing/fake-bridge';
import { tokens, usageByModel, UsageTab } from './usage-tab';

const events = [
  ev(1, 'usage', { run_id: 'r1', model: 'claude-opus-5-5', prompt_tokens: 1200, completion_tokens: 300, estimated: false }, { agent: 't' }),
  ev(2, 'usage', { run_id: 'r2', model: 'claude-sonnet-5', prompt_tokens: 800, completion_tokens: 100, cached_tokens: 400, estimated: true }, { agent: 't' }),
  ev(3, 'usage', { run_id: 'r3', model: 'claude-opus-5-5', prompt_tokens: 10_000, completion_tokens: 2000, estimated: false }, { agent: 't' }),
  ev(4, 'usage', { run_id: 'r4', model: 'claude-opus-5-5', prompt_tokens: 5, completion_tokens: 5, estimated: false }, { agent: 'other' }),
];

describe('usageByModel and tokens', () => {
  it("sums one agent's usage by model, in first-use order, and names token counts briefly", () => {
    expect(usageByModel(events, 't')).toEqual([
      { model: 'claude-opus-5-5', prompt: 11_200, completion: 2300, cached: 0, estimated: false, runs: 2 },
      { model: 'claude-sonnet-5', prompt: 800, completion: 100, cached: 400, estimated: true, runs: 1 },
    ]);
    expect([tokens(950), tokens(1500), tokens(12_345)]).toEqual(['950', '1.5k', '12k']);
  });
});

describe('tokens (UsageTab.test.ts)', () => {
  it('keeps small counts exact and scales large ones to k, M and B', () => {
    expect(tokens(236)).toBe('236');
    expect(tokens(7_700)).toBe('7.7k');
    expect(tokens(5_811_000)).toBe('5.8M');
    expect(tokens(402_266_000)).toBe('402M');
    expect(tokens(3_200_000_000)).toBe('3.2B');
  });

  it('moves to the next unit instead of printing 1000k', () => {
    expect(tokens(999_700)).toBe('1.0M');
    expect(tokens(9_960)).toBe('10k');
  });
});

describe('UsageTab', () => {
  it('tabulates calls and tokens per model with totals, and says when counts are estimates', async () => {
    await render(UsageTab, { inputs: { usage: usageByModel(events, 't') }, providers: new FakeDeskBridge().providers });
    const cells = (sel: string) => [...document.querySelectorAll(sel)].map((r) => [...r.children].map((c) => c.textContent));
    expect(cells('.usage-table thead tr')).toEqual([['Model', 'Calls', 'Prompt', 'Cached', 'Completion']]);
    expect(cells('.usage-table tbody tr')).toEqual([
      ['claude-opus-5-5', '2', '11,200', '0', '2,300'],
      ['claude-sonnet-5', '1', '800', '400', '100'],
    ]);
    expect(cells('.usage-table tfoot tr')).toEqual([['Total', '', '12,000', '', '2,400']]);
    expect(screen.getByText("Some counts are estimates; the model endpoint didn't report usage for every call.")).toBeTruthy();
  });

  it('explains an empty table', async () => {
    await render(UsageTab, { inputs: { usage: [] }, providers: new FakeDeskBridge().providers });
    expect(screen.getByRole('heading', { name: 'No usage yet' })).toBeTruthy();
    expect(screen.getByText("Token counts appear after the thread's first model call.")).toBeTruthy();
  });
});
