import { fireEvent, render, screen, within } from '@testing-library/angular';
import { describe, expect, it, vi } from 'vitest';
import { foldMessages, type MessagesState } from '@desk/client';
import { ev } from '@desk/client/testing';
import type { AgentMessageKind, EventOf } from '@desk/protocol';
import { clock } from '@desk/ui-core';
import { FakeDeskBridge } from '../testing/fake-bridge';
import { PairSheet } from './pair-sheet';

/** A time `m` minutes ago, 20 s past the minute so it reads "<m>m" on either side of the shared clock's 15 s step. */
const minutesAgo = (m: number) => new Date(Date.now() - m * 60_000 - 20_000).toISOString();
const TITLES: Record<string, string> = { d: 'Desk', a: 'Auth API', f: 'Frontend' };
const created = (id: number, agent: string) =>
  ev(id, 'agent.created', { role: agent === 'd' ? 'desk' : 'thread', model: 'm', title: TITLES[agent]!, brief: null, workspace_path: `/w/${agent}`, parent_id: agent === 'd' ? null : 'd' }, { agent });
const msg = (id: number, from: string, to: string, kind: AgentMessageKind, text: string, ts: string, extra: Partial<EventOf<'message.agent'>['payload']> = {}) =>
  ev(id, 'message.agent', { from_agent_id: from, from_label: from === 'd' ? 'Desk' : `thread "${TITLES[from]}" (${from})`, kind, text, ...extra }, { agent: to, ts });
const team = () => [created(1, 'd'), created(2, 'a'), created(3, 'f')];

async function show(messages: MessagesState, a: string, b: string, onClose: () => void = () => {}) {
  await render(`<div deskPairSheet projectId="p" [messages]="messages" [a]="a" [b]="b" (close)="onClose()"></div>`, {
    imports: [PairSheet],
    componentProperties: { messages, a, b, onClose },
    providers: new FakeDeskBridge().providers,
  });
}

describe('PairSheet', () => {
  it('nests each answer under its question with the question state, and links each message to a transcript', async () => {
    const asked = minutesAgo(6);
    const answered = minutesAgo(5);
    const messages = foldMessages([
      ...team(),
      msg(4, 'a', 'f', 'question', 'Which token format?', asked, { tracked: true }),
      msg(5, 'f', 'a', 'answer', 'JWT, RS256.', answered, { reply_to: 4 }),
      msg(6, 'f', 'a', 'note', 'Renamed the token field.', minutesAgo(4)),
      msg(7, 'f', 'a', 'question', 'Is /login stable?', minutesAgo(3), { tracked: true }),
    ]);
    const onClose = vi.fn();
    await show(messages, 'a', 'f', onClose);
    const sheet = screen.getByRole('dialog', { name: 'Auth API ⇄ Frontend' });
    const rows = [...sheet.querySelectorAll<HTMLElement>('.pair-rows > li')];
    expect(rows).toHaveLength(3);
    expect(rows[0]!.querySelector('.pair-head')!.textContent).toBe(`Auth API → Frontend · question · ${clock(asked)}`);
    expect(rows[0]!.textContent).toContain(`answered ${clock(answered)}`);
    const answer = rows[0]!.querySelector('.pair-answers')!;
    expect(answer.querySelector('.pair-head')!.textContent).toBe(`Frontend → Auth API · answer · ${clock(answered)}`);
    expect(answer.textContent).toContain('JWT, RS256.');
    expect(rows[1]!.querySelector('.pair-answers')).toBeNull();
    expect(rows[2]!.textContent).toContain('open · 3m');
    // The question shows in its recipient's transcript, the answer in the asker's.
    expect(within(rows[0]!).getAllByRole('link').map((l) => [l.textContent, l.getAttribute('href')])).toEqual([
      ['show in Frontend transcript', '#/p/p/threads/f?at=4'],
      ['show in Auth API transcript', '#/p/p/threads/a?at=5'],
    ]);
    fireEvent.click(within(rows[1]!).getByRole('link', { name: 'show in Auth API transcript' }));
    expect(onClose).toHaveBeenCalledTimes(1);
    fireEvent.click(within(sheet).getByRole('button', { name: 'Close' }));
    expect(onClose).toHaveBeenCalledTimes(2);
  });

  it('mutes the runtime closure of a question and names Desk second', async () => {
    const messages = foldMessages([
      ...team(),
      msg(4, 'd', 'f', 'question', 'Done yet?', minutesAgo(2), { tracked: true }),
      msg(5, 'f', 'd', 'answer', '(Frontend was stopped before answering.)', minutesAgo(1), { reply_to: 4, auto: true }),
    ]);
    await show(messages, 'd', 'f');
    const sheet = screen.getByRole('dialog', { name: 'Frontend ⇄ Desk' });
    const closure = sheet.querySelector('.pair-answers .pair-msg')!;
    expect(closure.className).toContain('muted');
    expect(closure.querySelector('.pair-head')!.textContent).toMatch(/^Frontend could not answer · \d\d:\d\d$/);
    expect(sheet.querySelector('.pair-rows > li .pair-state')!.textContent).toBe('closed');
  });
});
