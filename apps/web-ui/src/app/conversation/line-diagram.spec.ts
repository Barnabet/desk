import { fireEvent, render, screen } from '@testing-library/angular';
import { describe, expect, it, vi } from 'vitest';
import { foldMessages, type ProjectState, type TimelineState } from '@desk/client';
import { ev } from '@desk/client/testing';
import type { AttentionItem } from '@desk/protocol';
import { clock, lineGeometry } from '@desk/ui-core';
import { FakeDeskBridge } from '../testing/fake-bridge';
import { LineDiagram } from './line-diagram';

const NOW = Date.parse('2026-09-25T12:00:00.000Z');
/** `min` minutes before NOW. */
const at = (min: number) => new Date(NOW - min * 60_000).toISOString();

/** The brief three hours ago; Auth API (running, asked Frontend a tracked question 4 minutes ago) and Frontend (waiting). */
const timeline = (extraMarks: TimelineState['lanes'][number]['marks'] = []): TimelineState => ({
  deskId: 'd',
  start: at(180),
  stations: [{ eventId: 2, ts: at(180), kind: 'brief', label: 'Relaunch onboarding', threadIds: [] }],
  lanes: [
    { threadId: 'a', title: 'Auth API', forkedAt: at(20), status: 'running', archived: false, segments: [{ from: at(20), to: null, status: 'running' }], marks: [{ eventId: 5, ts: at(4), kind: 'question', label: 'Frontend' }] },
    { threadId: 'f', title: 'Frontend', forkedAt: at(15), status: 'waiting', archived: false, segments: [{ from: at(15), to: null, status: 'waiting' }], marks: extraMarks },
  ],
  approvals: {},
  lastSeq: 7,
});
const project = (approvals: unknown[] = []) =>
  ({
    project: { id: 'p', settings: { review_rounds: 2 } },
    desk: { id: 'd', status: 'running', model: 'claude-opus-5-5', model_override: null },
    threads: [
      { id: 'a', title: 'Auth API', status: 'running', activity: 'Reading the schema' },
      { id: 'f', title: 'Frontend', status: 'waiting', activity: null },
    ],
    approvals,
    plan: [],
    services: [],
    sources: [],
    lastSeq: 7,
  }) as unknown as ProjectState;
const messages = foldMessages([
  ev(1, 'agent.created', { role: 'desk', model: 'm', title: 'Desk', brief: null, workspace_path: '/w/d', parent_id: null }, { agent: 'd', ts: at(180) }),
  ev(3, 'agent.created', { role: 'thread', model: 'm', title: 'Auth API', brief: 'Own auth', workspace_path: '/w/a', parent_id: 'd' }, { agent: 'a', ts: at(20) }),
  ev(4, 'agent.created', { role: 'thread', model: 'm', title: 'Frontend', brief: 'Own the page', workspace_path: '/w/f', parent_id: 'd' }, { agent: 'f', ts: at(15) }),
  ev(5, 'message.agent', { from_agent_id: 'a', from_label: 'thread "Auth API" (a)', kind: 'question', text: 'Which token format?', tracked: true }, { agent: 'f', ts: at(4) }),
]);
const geometry = (now = NOW, t = timeline()) => lineGeometry({ timeline: t, threads: project().threads, now, width: 1200 });

const TEMPLATE = `<section deskLineDiagram [g]="g" [project]="project" [messages]="messages" [attention]="attention" [now]="now" (station)="station($event)" (pair)="pair($event)"></section>`;

async function show(o: { g?: ReturnType<typeof geometry>; project?: ProjectState } = {}) {
  const props = { g: o.g ?? geometry(), project: o.project ?? project(), messages, attention: [] as AttentionItem[], now: NOW, station: vi.fn(), pair: vi.fn() };
  const view = await render(TEMPLATE, { imports: [LineDiagram], componentProperties: props, providers: new FakeDeskBridge().providers });
  // rerender's detectChanges skips afterRender hooks; the tick it schedules runs them (the scroll pinning).
  const rerender = async (next: Partial<typeof props>) => {
    await view.rerender({ componentProperties: next, partialUpdate: true });
    await view.fixture.whenStable();
  };
  return { ...props, rerender };
}

/** The label-column entry titled `title`. */
const label = (title: string) => [...document.querySelectorAll('.line-label')].find((el) => el.querySelector('.line-label-title')?.textContent === title)!;

describe('LineDiagram', () => {
  it("draws Desk's trunk and stations, a lane per thread with its label and train, and the legend", async () => {
    await show();
    const diagram = screen.getByRole('region', { name: 'Line diagram: Desk and its threads since the brief' });
    expect(diagram.className).toBe('line-diagram');
    expect(diagram.style.height).toBe(`${geometry().height}px`);
    expect(diagram.querySelectorAll('svg.line-svg > g')).toHaveLength(2);
    expect(screen.getByRole('button', { name: `${clock(at(180))}, Relaunch onboarding` }).className).toBe('line-station line-station-brief');
    expect(diagram.querySelector('.line-station-label')!.textContent).toBe(`${clock(at(180))} Your brief · Relaunch onboarding`);
    expect(diagram.querySelector('.line-now')!.textContent).toBe(`now ${clock(NOW)}`);
    expect(screen.getByRole('link', { name: 'Auth API, running now' }).getAttribute('href')).toBe('#/p/p/threads/a');
    expect(diagram.querySelector('.line-activity')!.textContent).toBe('Reading the schema');
    expect(diagram.querySelector('.line-desk-writing')!.textContent).toBe('Desk · writing');
    expect([...diagram.querySelectorAll('.line-label')].map((l) => [l.querySelector('.line-label-title')!.textContent, l.querySelector('.line-label-sub')!.textContent, l.querySelector('.line-label-sub')!.className])).toEqual([
      ['Desk', 'writing · opus-5-5', 'line-label-sub status-text-running'],
      ['Auth API', 'running · 20m', 'line-label-sub status-text-running'],
      ['Frontend', 'waiting · 15m', 'line-label-sub status-text-waiting'],
    ]);
    expect(label('Frontend').getAttribute('href')).toBe('#/p/p/threads/f');
    expect(diagram.querySelector('.line-legend')!.textContent).toBe('Deskrunningdonewaitingneeds youquestionfallback');
  });

  it('jumps the chat to a station', async () => {
    const { station } = await show();
    fireEvent.click(screen.getByRole('button', { name: `${clock(at(180))}, Relaunch onboarding` }));
    expect(station).toHaveBeenCalledWith(expect.objectContaining({ eventId: 2, kind: 'brief' }));
  });

  it("marks a tracked question on its asker's lane, lights its recipient's label and opens the pair", async () => {
    const { pair } = await show();
    const mark = screen.getByRole('button', { name: `Auth API asked Frontend, ${clock(at(4))}` });
    expect(mark.className).toBe('line-q line-q-open');
    expect(mark.getAttribute('title')).toBe(`Auth API asked Frontend · ${clock(at(4))} · open`);
    fireEvent.mouseEnter(mark);
    expect(label('Frontend').classList.contains('lit')).toBe(true);
    expect(label('Auth API').classList.contains('lit')).toBe(false);
    fireEvent.mouseLeave(mark);
    expect(label('Frontend').classList.contains('lit')).toBe(false);
    fireEvent.focus(mark);
    expect(label('Frontend').classList.contains('lit')).toBe(true);
    fireEvent.blur(mark);
    expect(label('Frontend').classList.contains('lit')).toBe(false);
    fireEvent.click(mark);
    expect(pair).toHaveBeenCalledWith(['a', 'f']);
  });

  it("points a lane that waits for approval at the approval in Attention", async () => {
    const t = timeline([{ eventId: 7, ts: at(2), kind: 'signal', label: 'bash' }]);
    await show({ g: geometry(NOW, t), project: project([{ id: 'x1', agent_id: 'f', delegate_to_desk: false }]) });
    const signal = document.querySelector<HTMLAnchorElement>('.line-signal')!;
    expect(signal.getAttribute('href')).toBe('#/attention?item=approval%3Ax1');
    expect(signal.querySelector('.line-signal-chip')!.textContent).toBe(`Waiting for your approval · bash · ${clock(at(2))}`);
  });

  it('follows "now" until the user scrolls back into history, and again once they return', async () => {
    const view = await show();
    const scroller = document.querySelector<HTMLElement>('.line-scroll')!;
    let left = 0;
    Object.defineProperty(scroller, 'scrollWidth', { configurable: true, get: () => 3000 });
    Object.defineProperty(scroller, 'clientWidth', { configurable: true, get: () => 860 });
    Object.defineProperty(scroller, 'scrollLeft', { configurable: true, get: () => left, set: (v: number) => (left = v) });
    // A minute later the history is wider and "now" further right: the view follows it.
    await view.rerender({ g: geometry(NOW + 60_000), now: NOW + 60_000 });
    expect(left).toBe(3000);
    left = 1000;
    fireEvent.scroll(scroller);
    await view.rerender({ g: geometry(NOW + 120_000), now: NOW + 120_000 });
    expect(left).toBe(1000);
    left = 2140; // within 8 px of the right edge
    fireEvent.scroll(scroller);
    await view.rerender({ g: geometry(NOW + 180_000), now: NOW + 180_000 });
    expect(left).toBe(3000);
  });
});
