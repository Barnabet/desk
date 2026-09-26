import { fireEvent, render, screen, waitFor, within } from '@testing-library/angular';
import { describe, expect, it, vi } from 'vitest';
import type { ChatItem } from '@desk/client';
import { WAKES_PAUSED } from '@desk/protocol';
import { clock, type RowView } from '@desk/ui-core';
import { FakeDeskBridge, type FakeHandlers } from '../testing/fake-bridge';
import { agentLabel, chatDomId, ChatItemView } from './chat-items';

const TS = '2026-09-25T09:30:00.000Z';
const TEMPLATE = `<div deskChatItem [item]="item" projectId="p" [attentionIds]="attentionIds" [answering]="answering" [view]="view" [now]="now" deskId="d" (answer)="answer($event)" (ownWords)="ownWords()" (jump)="jump($event)" (pair)="pair($event)"></div>`;

/** Renders one row; `rerender` changes what the screen would pass it next (the rest stays as it was). */
async function row(item: ChatItem, o: { view?: RowView; attentionIds?: string[]; answering?: string | null; now?: number; handlers?: FakeHandlers } = {}) {
  const bridge = new FakeDeskBridge(o.handlers ?? {});
  const props = { item, view: o.view, attentionIds: new Set(o.attentionIds ?? []), answering: o.answering ?? null, now: o.now, answer: vi.fn(), ownWords: vi.fn(), jump: vi.fn(), pair: vi.fn() };
  const view = await render(TEMPLATE, { imports: [ChatItemView], componentProperties: props, providers: bridge.providers });
  const host = view.container.querySelector<HTMLElement>('.chat-item')!;
  return { ...props, bridge, host, rerender: (next: Partial<typeof props>) => view.rerender({ componentProperties: next, partialUpdate: true }) };
}

describe('ChatItemView', () => {
  it('names threads by title, and each row by the id the screen jumps to', async () => {
    expect(agentLabel('thread "Auth API" (a1)')).toBe('Auth API');
    expect(agentLabel('Desk')).toBe('Desk');
    expect(chatDomId('e:12')).toBe('chat-e-12');
    expect(chatDomId('run:r/1')).toBe('chat-run-r-1');
    const { host } = await row({ kind: 'user', id: 'e:3', ts: TS, text: 'Kick off' });
    expect(host.id).toBe('chat-e-3');
    expect(host.querySelector('.chat-user')!.textContent).toBe(`You · ${clock(TS)}Kick off`);
  });

  it("shows Desk's reply in its voice, live while it streams", async () => {
    const r = await row({ kind: 'assistant', id: 'run:r1', ts: TS, runId: 'r1', text: 'Plan **ready**.', streaming: true });
    const desk = r.host.querySelector<HTMLElement>('.chat-desk')!;
    expect(desk.getAttribute('aria-live')).toBe('polite');
    expect(desk.querySelector('.chat-meta')!.textContent).toBe('Desk · writing');
    expect(desk.querySelector('.chat-meta .live-dot')).toBeTruthy();
    expect(desk.querySelector('.md-voice strong')!.textContent).toBe('ready');
    await r.rerender({ item: { kind: 'assistant', id: 'run:r1', ts: TS, runId: 'r1', text: 'Plan ready.', streaming: false, interrupted: true } });
    expect(desk.getAttribute('aria-live')).toBeNull();
    expect(desk.querySelector('.chat-meta')!.textContent).toBe(`Desk · ${clock(TS)}`);
    expect(desk.textContent).toContain('Cut off by an error; Desk will pick up again.');
  });

  it("answers Desk's question with an option or in your own words, and waits while one is sending", async () => {
    const question: ChatItem = { kind: 'question', id: 'e:6', ts: TS, eventId: 6, question: 'Data source or teammate invite first?', options: ['Connect a data source', 'Invite a teammate'], answered: false };
    const r = await row(question);
    const card = r.host.querySelector<HTMLElement>('.question-card')!;
    expect(card.getAttribute('aria-labelledby')).toBe('question-6');
    expect(card.querySelector('#question-6')!.textContent).toBe('Data source or teammate invite first?');
    expect(card.querySelector('.eyebrow')!.textContent).toBe(`Desk asks · ${clock(TS)}`);
    const first = screen.getByRole('button', { name: 'Connect a data source' });
    expect(first.className).toContain('btn-primary');
    expect(screen.getByRole('button', { name: 'Invite a teammate' }).className).toContain('btn-secondary');
    fireEvent.click(first);
    expect(r.answer).toHaveBeenCalledWith('Connect a data source');
    fireEvent.click(screen.getByRole('button', { name: 'Answer in your own words…' }));
    expect(r.ownWords).toHaveBeenCalledTimes(1);
    await r.rerender({ answering: 'Invite a teammate' });
    expect((screen.getByRole('button', { name: 'Connect a data source' }) as HTMLButtonElement).disabled).toBe(true);
    expect(screen.getByRole('button', { name: 'Invite a teammate' }).getAttribute('aria-busy')).toBe('true');
    await r.rerender({ item: { ...question, answered: true }, answering: null });
    expect(card.className).toBe('question-card answered');
    expect(within(card).queryAllByRole('button')).toEqual([]);
    expect(within(card).getByText('Answered')).toBeTruthy();
  });

  it("links a report's results to the Library, and its needs to Attention while they are listed", async () => {
    await row(
      { kind: 'report', id: 'e:5', ts: TS, eventId: 5, headline: 'Research is in', progress: 'All **three** lead with one first win.', needsYou: ['Approve installing bun', 'Pick a name'], results: ['competitor-onboarding.md'] },
      { attentionIds: ['report:5:0'] },
    );
    const report = screen.getByRole('article', { name: 'Research is in' });
    expect(report.querySelector('.eyebrow')!.textContent).toBe(`Report · ${clock(TS)}`);
    expect(report.querySelector('.report-progress strong')!.textContent).toBe('three');
    expect(within(report).getByRole('link', { name: 'competitor-onboarding.md' }).getAttribute('href')).toBe('#/p/p/library?file=competitor-onboarding.md');
    expect(report.querySelector('.report-needs strong')!.textContent).toBe('Needs you · 2');
    expect(within(report).getByRole('link', { name: 'Approve installing bun' }).getAttribute('href')).toBe('#/attention?item=report%3A5%3A0');
    // Handled already: plain text.
    expect(within(report).getByText('Pick a name').className).toBe('needs-done');
    expect([...report.querySelectorAll('.needs-num')].map((n) => n.textContent)).toEqual(['1', '2']);
  });

  it('offers Resume on the paused notice while its item is listed, and words the proxy outage calmly', async () => {
    const r = await row(
      { kind: 'notice', id: 'e:5', ts: TS, level: 'warning', code: WAKES_PAUSED, message: 'Agents woke each other 60 times in the last hour, so automatic wakes are paused.' },
      { attentionIds: ['paused:5'], handlers: { 'attention.dismiss': () => ({ ok: true }) } },
    );
    const note = screen.getByRole('status');
    expect(note.className).toBe('chat-notice notice-warning');
    expect(note.querySelector('.dot')!.className).toBe('dot warn');
    fireEvent.click(within(note).getByRole('button', { name: 'Resume' }));
    await waitFor(() => expect(r.bridge.calls).toEqual([{ channel: 'attention.dismiss', input: { id: 'paused:5' } }]));
    await r.rerender({ attentionIds: new Set<string>() });
    expect(within(note).queryByRole('button', { name: 'Resume' })).toBeNull();
    expect(note.textContent).toContain('automatic wakes are paused');
    await r.rerender({ item: { kind: 'notice', id: 'e:7', ts: TS, level: 'info', code: 'proxy_down', message: 'proxy_down: ECONNREFUSED' } });
    expect(screen.getByRole('status').textContent).toBe('Paused, will resume: the model proxy is unreachable.');
    expect(screen.getByRole('status').querySelector('.dot')!.className).toBe('dot ok');
  });

  it("clamps a long message to three lines until more, and opens the pair from the recipient's name", async () => {
    const long = Array.from({ length: 6 }, (_, i) => `Step ${i + 1} of the plan.`).join('\n');
    const r = await row({ kind: 'message', id: 'e:6', ts: TS, eventId: 6, from: 'd', to: 'a', messageKind: 'note', text: long }, { view: { toTitle: 'Auth API' } });
    expect(r.host.querySelector('.msg-head')!.textContent).toBe(`Desk → Auth API · note · ${clock(TS)}`);
    expect(r.host.querySelector('.chat-msg')!.className).toBe('chat-msg msg-note');
    expect(r.host.querySelector('.feed-text')!.classList.contains('clamp-3')).toBe(true);
    fireEvent.click(screen.getByRole('button', { name: 'more' }));
    expect(r.host.querySelector('.feed-text')!.classList.contains('clamp-3')).toBe(false);
    expect(screen.getByRole('button', { name: 'less' })).toBeTruthy();
    fireEvent.click(screen.getByRole('button', { name: 'Auth API' }));
    expect(r.pair).toHaveBeenCalledWith(['a', 'd']);
    await r.rerender({ item: { kind: 'steer', id: 'e:8', ts: TS, eventId: 8, to: 'f', text: 'How did you price it?', question: true }, view: { toTitle: 'Frontend' } });
    expect(r.host.querySelector('.msg-head')!.textContent).toBe(`You asked Frontend · ${clock(TS)}`);
    expect(screen.getByRole('link', { name: 'Frontend' }).getAttribute('href')).toBe('#/p/p/threads/f');
    expect(screen.queryByRole('button', { name: 'more' })).toBeNull();
  });

  it("shows a tracked question's state, and jumps to its answer", async () => {
    const now = Date.parse(TS) + 4 * 60_000 + 20_000;
    const r = await row(
      { kind: 'message', id: 'e:6', ts: TS, eventId: 6, from: 'd', to: 'a', messageKind: 'question', text: 'Which token format?' },
      { view: { toTitle: 'Auth API', question: { state: 'open', since: TS, toTitle: 'Auth API' } }, now },
    );
    expect(r.host.querySelector('.msg-state')!.className).toBe('msg-state msg-open');
    expect(r.host.querySelector('.msg-state')!.textContent).toBe('waiting for an answer · 4m');
    const answeredAt = '2026-09-25T09:36:00.000Z';
    await r.rerender({ view: { toTitle: 'Auth API', question: { state: 'answered', since: TS, answerId: 9, answeredAt, toTitle: 'Auth API' } }, now: undefined });
    fireEvent.click(screen.getByRole('button', { name: `answered ${clock(answeredAt)} ↓` }));
    expect(r.jump).toHaveBeenCalledWith('e:9');
    await r.rerender({ view: { toTitle: 'Auth API', question: { state: 'withdrawn', since: TS, toTitle: 'Auth API' } } });
    expect(r.host.querySelector('.msg-state')!.className).toBe('msg-state muted');
    expect(r.host.querySelector('.msg-state')!.textContent).toBe('withdrawn');
  });

  it("says why a thread could not answer, names the thread's own messages as a pair and the runtime's as a link", async () => {
    const closure: ChatItem = { kind: 'agent', id: 'e:10', ts: TS, fromAgentId: 'f', fromLabel: 'thread "Frontend" (f)', messageKind: 'answer', text: '(Frontend was stopped before answering.)', replyTo: 9, auto: true };
    const r = await row(closure, { view: { answers: { question: 9, asker: 'Desk', text: 'Done yet?' } } });
    expect(r.host.querySelector('.feed-closed-text')!.textContent).toBe('Frontend could not answer: it was stopped before answering.');
    expect(r.host.textContent).not.toContain('Answer');
    fireEvent.click(screen.getByRole('button', { name: "↩ Desk's question: “Done yet?”" }));
    expect(r.jump).toHaveBeenCalledWith('e:9');
    // A closure that already says it could not answer is not said twice.
    await r.rerender({ item: { ...closure, text: '(Frontend could not answer: Desk was restarting. Ask again if you still need to know.)' } });
    expect(r.host.querySelector('.feed-closed-text')!.textContent).toBe('Frontend could not answer: Desk was restarting. Ask again if you still need to know.');
    await r.rerender({ item: { kind: 'agent', id: 'e:11', ts: TS, fromAgentId: 'f', fromLabel: 'thread "Frontend" (f)', messageKind: 'update', text: 'Halfway there.' }, view: undefined });
    expect(r.host.querySelector('.chat-feed')!.className).toBe('chat-feed feed-update');
    expect(r.host.querySelector('.feed-chip')!.textContent).toBe('Update');
    fireEvent.click(screen.getByRole('button', { name: 'Frontend' }));
    expect(r.pair).toHaveBeenCalledWith(['f', 'd']);
    await r.rerender({ item: { kind: 'agent', id: 'e:12', ts: TS, fromAgentId: 'f', fromLabel: 'thread "Frontend" (f)', messageKind: 'completed', text: 'Frontend ready.' } });
    expect(r.host.querySelector('.feed-chip')!.textContent).toBe('Result');
    expect(screen.getByRole('link', { name: 'Frontend' }).getAttribute('href')).toBe('#/p/p/threads/f');
  });

  it("drops Desk's successful sends from its tool rows, and names the rest", async () => {
    const ok = { id: 'c1', name: 'message_thread', arguments: '{"thread_id":"a","text":"Use JWT."}', status: 'ok' as const, content: null };
    const r = await row({ kind: 'tools', id: 'tools:5', ts: TS, calls: [ok] });
    expect(r.host.textContent).toBe('');
    expect(r.host.childElementCount).toBe(0);
    await r.rerender({
      item: { kind: 'tools', id: 'tools:5', ts: TS, calls: [ok, { ...ok, id: 'c2', status: 'error' }, { id: 'c3', name: 'read_thread', arguments: '{"thread_id":"a"}', status: 'running', content: null }] },
      view: { titles: { a: 'Auth API' } },
    });
    expect(r.host.querySelector('.toolgroup-title')!.textContent).toBe('Desk is using 2 tools');
    expect([...r.host.querySelectorAll('.toolgroup li .mono')].map((l) => l.textContent)).toEqual(['message_thread Auth API', 'read_thread Auth API']);
  });

  it("lists a digest's pairs on demand, each opening the pair's sheet", async () => {
    const now = Date.parse(TS) + 3 * 60_000 + 20_000;
    const r = await row(
      { kind: 'digest', id: 'e:5', ts: TS, eventId: 5, messageIds: [5, 6] },
      { view: { digest: { messages: 2, open: 1, pairs: [{ key: 'a f', label: 'Auth API ⇄ Frontend', count: 2, waiting: { who: 'Auth API', since: TS }, a: 'a', b: 'f' }] } }, now },
    );
    const toggle = screen.getByRole('button', { name: 'Between threads · 2 messages · 1 open question' });
    expect(toggle.getAttribute('aria-expanded')).toBe('false');
    expect(r.host.querySelector('.digest-pairs')).toBeNull();
    fireEvent.click(toggle);
    expect(toggle.getAttribute('aria-expanded')).toBe('true');
    fireEvent.click(screen.getByRole('button', { name: 'Auth API ⇄ Frontend · 2 · Auth API waiting 3m' }));
    expect(r.pair).toHaveBeenCalledWith(['a', 'f']);
    await r.rerender({ item: { kind: 'compacted', id: 'c:9', ts: TS }, view: undefined });
    expect(screen.getByRole('separator').textContent).toBe('Earlier conversation summarised');
  });
});
