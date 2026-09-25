import { describe, expect, it } from 'vitest';
import { foldMessages } from '@desk/client';
import { ev } from '@desk/client/testing';
import type { AttentionItem } from '@desk/protocol';
import { answeringLabel, waitHop, waitLabel, waitsOnYou } from './waits';

const NOW = Date.UTC(2026, 8, 24, 11, 0, 0);
/** `m` minutes before NOW. */
const ago = (m: number) => new Date(NOW - m * 60_000).toISOString();
const TITLES: Record<string, string> = { d: 'Desk', a: 'Auth API', b: 'Billing', f: 'Frontend' };
const created = (id: number, agent: string) =>
  ev(id, 'agent.created', { role: agent === 'd' ? 'desk' : 'thread', model: 'm', title: TITLES[agent]!, brief: null, workspace_path: `/w/${agent}`, parent_id: agent === 'd' ? null : 'd' }, { agent });
/** A tracked question from `from` to `to`, asked `minutes` before NOW. */
const ask = (id: number, from: string, to: string, minutes: number) =>
  ev(id, 'message.agent', { from_agent_id: from, from_label: from === 'd' ? 'Desk' : `thread "${TITLES[from]}" (${from})`, kind: 'question', text: 'q', tracked: true }, { agent: to, ts: ago(minutes) });
const team = () => [created(1, 'd'), created(2, 'a'), created(3, 'b'), created(4, 'f')];
const item = (agentId: string, minutes: number, kind: AttentionItem['kind'] = 'approval'): AttentionItem => ({
  id: `${kind}:${agentId}`,
  kind,
  project_id: 'p',
  project_name: 'P',
  agent_id: agentId,
  title: 't',
  detail: '',
  created_at: ago(minutes),
  ref: {},
});

describe('waitLabel', () => {
  it("names what a thread waits on from its open questions, with the oldest one's age", () => {
    const m = foldMessages([...team(), ask(5, 'a', 'f', 4), ask(6, 'a', 'd', 2), ask(7, 'b', 'd', 1)]);
    expect(waitLabel(m, 'a', [], NOW)).toBe('waiting on Frontend and Desk · 4m');
    expect(waitLabel(m, 'b', [], NOW)).toBe('waiting on Desk · 1m');
    // No open question and no attention item: nothing to name.
    expect(waitLabel(m, 'f', [], NOW)).toBeNull();
  });

  it('says "waiting on you" only for an agent with an attention item', () => {
    const m = foldMessages([...team(), ask(5, 'b', 'd', 1)]);
    expect(waitLabel(m, 'f', [item('f', 3)], NOW)).toBe('waiting on you · 3m');
    // Desk's own question to the user does not make a thread that waits on Desk wait on you.
    expect(waitLabel(m, 'b', [item('d', 2, 'question')], NOW)).toBe('waiting on Desk · 1m');
  });

  it('keeps naming what a stalled thread waits on: a stall asks nothing of the user', () => {
    const m = foldMessages([...team(), ask(5, 'a', 'b', 16)]);
    expect(waitLabel(m, 'a', [item('a', 0, 'stalled')], NOW)).toBe('waiting on Billing · 16m');
    expect(waitsOnYou(item('a', 0, 'stalled'))).toBe(false);
    expect(waitsOnYou(item('a', 0, 'approval'))).toBe(true);
  });
});

describe('answeringLabel', () => {
  it('names the asker while an answer run is in progress', () => {
    const run = (id: number, thread: string, q: number, runId: string) => ev(id, 'run.started', { run_id: runId, model: 'm', answering: q }, { agent: thread });
    const m = foldMessages([
      ...team(),
      ask(5, 'a', 'f', 3),
      ask(6, 'd', 'b', 2),
      ev(7, 'message.user', { text: 'How did you price it?', question: true }, { agent: 'a' }),
      run(8, 'f', 5, 'r1'),
      run(9, 'b', 6, 'r2'),
      run(10, 'a', 7, 'r3'),
    ]);
    expect([answeringLabel(m, 'f'), answeringLabel(m, 'b'), answeringLabel(m, 'a'), answeringLabel(m, 'd')]).toEqual(['answering Auth API', 'answering Desk', 'answering you', null]);
    const later = foldMessages([ev(11, 'run.finished', { run_id: 'r1', reason: 'no_tool_calls' }, { agent: 'f' })], m);
    expect(answeringLabel(later, 'f')).toBeNull();
  });
});

describe('waitHop', () => {
  it('follows a wait one hop to an agent that needs the user', () => {
    const m = foldMessages([...team(), ask(5, 'a', 'f', 4), ask(6, 'b', 'f', 3), ask(7, 'f', 'd', 2)]);
    const approval = item('f', 1);
    // a waits on Frontend, which needs your approval itself.
    expect(waitHop(m, 'a', [approval])).toEqual({ text: '→ needs your approval', label: 'Frontend needs your approval', item: approval });
    // b waits on Frontend, which waits on Desk, which needs your answer: one hop past Frontend.
    const deskAsks = item('d', 1, 'question');
    expect(waitHop(m, 'b', [deskAsks])).toEqual({ text: '→ Desk needs your answer', label: 'Desk needs your answer', item: deskAsks });
  });

  it('has no hop when nothing further needs the user, or when the agent itself does', () => {
    const m = foldMessages([...team(), ask(5, 'a', 'f', 4)]);
    expect(waitHop(m, 'a', [])).toBeNull();
    expect(waitHop(m, 'a', [item('a', 1), item('f', 1)])).toBeNull();
    expect(waitHop(m, 'f', [item('a', 1)])).toBeNull();
  });

  it("keeps the hop of a stalled thread, whose own item asks nothing of the user (waitLabel's rule)", () => {
    const m = foldMessages([...team(), ask(5, 'a', 'f', 4)]);
    const approval = item('f', 1);
    expect(waitHop(m, 'a', [item('a', 0, 'stalled'), approval])).toEqual({ text: '→ needs your approval', label: 'Frontend needs your approval', item: approval });
  });
});
