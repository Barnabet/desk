import { fireEvent, render, screen } from '@testing-library/angular';
import { describe, expect, it, vi } from 'vitest';
import { emptyMessages, type TranscriptEntry } from '@desk/client';
import { narrate, stopsOf } from '@desk/ui-core';
import { RouteView } from './route-view';

const TS = '2026-09-24T10:18:00.000Z';
const brief: TranscriptEntry = { kind: 'brief', id: 'e:1', ts: TS, text: 'Draft five emails.' };
const tools = (status: 'ok' | 'running'): TranscriptEntry => ({
  kind: 'tools',
  id: 'tools:3',
  ts: TS,
  calls: [{ id: 'c1', name: 'read_file', arguments: '{"path":"brief.md"}', status, content: status === 'ok' ? 'the brief' : null }],
});
const detour: TranscriptEntry = { kind: 'detour', id: 'e:5', ts: TS, from: 'claude-opus-5-5', to: 'claude-sonnet-5', reason: 'rate_limited' };
const result: TranscriptEntry = { kind: 'result', id: 'e:6', ts: TS, summary: 'Five drafts published', artifacts: [] };

async function mount(entries: TranscriptEntry[], o: { running?: boolean; activity?: string | null; selected?: number | null } = {}) {
  const view = await render(RouteView, {
    inputs: { stops: stopsOf(narrate(entries)), running: o.running ?? false, activity: o.activity ?? null, reviewRounds: 2, messages: emptyMessages(), selected: o.selected ?? null },
  });
  const picked = vi.fn();
  view.fixture.componentInstance.selectStop.subscribe(picked);
  return { view, picked };
}

describe('RouteView', () => {
  it('numbers each stop, names it for screen readers, draws its glyph and label, and explains the route', async () => {
    await mount([brief, tools('ok'), detour, result]);
    const stop = (n: number) => screen.getByRole('button', { name: new RegExp(`^Stop ${n}: `) });
    expect(stop(1).getAttribute('aria-label')).toMatch(/^Stop 1: Brief from Desk, \d\d:\d\d$/);
    expect(stop(2).getAttribute('aria-label')).toMatch(/^Stop 2: Used 1 tool, read_file · \d\d:\d\d$/);
    expect(stop(3).getAttribute('aria-label')).toMatch(/^Stop 3: Rate limited · a short detour, continued on claude-sonnet-5 · \d\d:\d\d$/);
    expect(stop(4).getAttribute('aria-label')).toMatch(/^Stop 4: Reported to Desk, Five drafts published · \d\d:\d\d$/);
    expect([1, 2, 3, 4].map((n) => stop(n).textContent)).toEqual(['Brief', '1 tool', 'detour', 'Report']);
    expect(stop(2).className).toBe('route-stop route-stop-work');
    expect([...document.querySelectorAll('.route-num')].map((n) => n.textContent)).toEqual(['1', '2', '3', '4']);
    // A detour's label sits above its disc; the others below.
    expect(document.querySelectorAll('.route-label.above')).toHaveLength(1);
    expect(stop(3).parentElement!.querySelector('.route-label.above .route-label-title')!.textContent).toBe('Rate limited · a short detour');
    // Three pieces of path between four stops, and no "now" while the thread is not running.
    expect(document.querySelectorAll('.route-svg path')).toHaveLength(3);
    expect(document.querySelector('.route-now')).toBeNull();
    expect(screen.queryByText('Next: report to Desk')).toBeNull();
    expect(document.querySelector('.route-legend')!.textContent).toBe(
      'Route takenLive since sent backNextDetour = continued on the fallback modelNumbers match the transcript',
    );
  });

  it('presses the selected stop and tells which one was clicked', async () => {
    const { picked } = await mount([brief, tools('ok'), result], { selected: 2 });
    const second = screen.getByRole('button', { name: /^Stop 2: / });
    expect(second.getAttribute('aria-pressed')).toBe('true');
    expect(second.className).toBe('route-stop route-stop-work selected');
    expect(screen.getByRole('button', { name: /^Stop 1: / }).getAttribute('aria-pressed')).toBe('false');
    fireEvent.click(screen.getByRole('button', { name: /^Stop 3: / }));
    expect(picked).toHaveBeenCalledWith(3);
  });

  it('shows where a running thread is now and what comes next', async () => {
    await mount([brief, tools('running')], { running: true, activity: 'bash wc -w emails/*.md' });
    const live = screen.getByRole('button', { name: /^Stop 2: Using 1 tool, / });
    expect(live.className).toBe('route-stop route-stop-work live');
    expect(document.querySelector('.route-now')).toBeTruthy();
    expect(document.querySelector('.route-label-title.run')!.textContent).toBe('Now · bash');
    expect(document.querySelector('.route-label-sub.mono')!.textContent).toBe('wc -w emails/*.md');
    expect(screen.getByText('Next: report to Desk')).toBeTruthy();
    const paths = [...document.querySelectorAll('.route-svg path')];
    expect(paths).toHaveLength(3);
    expect(paths.at(-1)!.getAttribute('stroke-dasharray')).toBe('4 5');
  });

  it('draws the live stretch after a revision, and counts the messages from other threads beside their stop', async () => {
    const revision: TranscriptEntry = { kind: 'revision', id: 'e:7', ts: TS, round: 1, feedback: 'Less salesy' };
    const note: TranscriptEntry = { kind: 'incoming', id: 'e:8', ts: TS, fromAgentId: 'a', fromLabel: 'thread "Auth API" (a)', messageKind: 'note', text: 'The API moved to v2.' };
    await mount([brief, revision, note], { running: true });
    const back = screen.getByRole('button', { name: /^Stop 2: Desk sent it back, round 1 of 2 · / });
    expect(back.textContent).toBe('R1');
    const messages = screen.getByRole('button', { name: /^Stop 3: / });
    expect(messages.getAttribute('aria-label')).toMatch(/^Stop 3: Messages, \d\d:\d\d, 1 message$/);
    expect(messages.textContent).toBe('✉');
    expect(document.querySelector('.route-cards')!.textContent).toBe('1 ✉');
    // Brief → revision is the route taken; revision → messages → now is live; now → next is dashed.
    const paths = [...document.querySelectorAll('.route-svg path')].map((p) => [p.getAttribute('stroke'), p.getAttribute('stroke-width')]);
    expect(paths).toEqual([
      ['var(--ink)', '2'],
      ['var(--run)', '2.5'],
      ['var(--run)', '2.5'],
      ['var(--muted)', '2'],
    ]);
  });
});
