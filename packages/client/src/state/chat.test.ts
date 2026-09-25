import { describe, expect, it } from 'vitest';
import type { AgentMessageKind, EventOf } from '@desk/protocol';
import { ev } from '../testing';
import { applyChatDelta, emptyChat, reduceChat } from './chat';

const delta = (run: string, text: string) => ({ type: 'assistant.delta' as const, project_id: 'p', agent_id: 'd', payload: { run_id: run, text } });

describe('reduceChat', () => {
  it('builds the Desk conversation with streaming, tools, reports, questions and notices', () => {
    let s = emptyChat('d');
    s = reduceChat(s, ev(1, 'message.user', { text: 'Relaunch onboarding' }, { agent: 'd' }));
    s = applyChatDelta(s, delta('r1', 'Got '));
    s = applyChatDelta(s, delta('r1', 'it.'));
    expect(s.items.at(-1)).toMatchObject({ kind: 'assistant', runId: 'r1', text: 'Got it.', streaming: true });
    s = reduceChat(s, ev(2, 'assistant.message', { run_id: 'r1', content: 'Got it. Four threads.', tool_calls: [] }, { agent: 'd' }));
    s = reduceChat(s, ev(3, 'tool.call', { run_id: 'r1', tool_call_id: 'c1', name: 'spawn_thread', arguments: '{"title":"A"}' }, { agent: 'd' }));
    s = reduceChat(s, ev(4, 'tool.call', { run_id: 'r1', tool_call_id: 'c2', name: 'spawn_thread', arguments: '{"title":"B"}' }, { agent: 'd' }));
    s = reduceChat(s, ev(5, 'tool.result', { run_id: 'r1', tool_call_id: 'c1', name: 'spawn_thread', status: 'ok', content: 'spawned' }, { agent: 'd' }));
    s = reduceChat(s, ev(6, 'message.agent', { from_agent_id: 't', from_label: 'thread "A"', kind: 'completed', text: 'Done' }, { agent: 'd' }));
    s = reduceChat(s, ev(7, 'report', { headline: 'Research is in', progress: 'p', needs_you: ['Approve bun'], results: ['a.md'] }, { agent: 'd' }));
    s = reduceChat(s, ev(8, 'question.asked', { question: 'Data source first?', options: ['Yes', 'No'] }, { agent: 'd' }));
    s = reduceChat(s, ev(9, 'system.notice', { level: 'error', code: 'proxy_down', message: 'paused' }));
    s = reduceChat(s, ev(10, 'tool.call', { run_id: 'r2', tool_call_id: 'x', name: 'bash', arguments: '{}' }, { agent: 'other' }));
    expect(s.items.map((i) => i.kind)).toEqual(['user', 'assistant', 'tools', 'agent', 'report', 'question', 'notice']);
    expect(s.items[1]).toMatchObject({ text: 'Got it. Four threads.', streaming: false, id: 'e:2' });
    expect(s.items[2]).toMatchObject({ kind: 'tools', calls: [{ id: 'c1', status: 'ok', content: 'spawned' }, { id: 'c2', status: 'running' }] });
    expect(s.items[5]).toMatchObject({ kind: 'question', answered: false, options: ['Yes', 'No'] });
    s = reduceChat(s, ev(11, 'message.user', { text: 'Yes' }, { agent: 'd' }));
    expect(s.items[5]).toMatchObject({ answered: true });
  });

  it('drops an empty streamed run and keeps a stream cut off by an error', () => {
    let s = applyChatDelta(emptyChat('d'), delta('r1', 'partial'));
    s = reduceChat(s, ev(1, 'assistant.message', { run_id: 'r1', content: null, tool_calls: [{ id: 'c', name: 'x', arguments: '{}' }] }, { agent: 'd' }));
    expect(s.items).toEqual([]);
    s = applyChatDelta(s, delta('r2', 'half a sent'));
    s = reduceChat(s, ev(2, 'run.finished', { run_id: 'r2', reason: 'error', detail: 'boom' }, { agent: 'd' }));
    expect(s.items).toEqual([expect.objectContaining({ kind: 'assistant', text: 'half a sent', streaming: false, interrupted: true })]);
    s = reduceChat(s, ev(3, 'context.compacted', { run_id: 'r3', checkpoint: 'x', up_to: 2, trigger: 'threshold' }, { agent: 'd' }));
    expect(s.items.at(-1)).toMatchObject({ kind: 'compacted' });
  });
});

describe('reduceChat: messages to threads, steers and the digest', () => {
  /** `ev()` stamps event `id` this many seconds after 10:00:00Z on 2026-09-24. */
  const ts = (id: number) => new Date(Date.UTC(2026, 8, 24, 10, 0, id)).toISOString();
  const d = { agent: 'd' };
  type Extra = Partial<EventOf<'message.agent'>['payload']>;
  const thread = (id: number, agent: string, title: string) =>
    ev(id, 'agent.created', { role: 'thread', model: 'm', title, brief: 'b', workspace_path: `/w/${agent}`, parent_id: 'd' }, { agent });
  const fromDesk = (id: number, to: string, kind: AgentMessageKind, extra: Extra = {}) =>
    ev(id, 'message.agent', { from_agent_id: 'd', from_label: 'Desk', kind, text: `${kind} ${id}`, ...extra }, { agent: to });
  const between = (id: number, from: string, to: string, kind: AgentMessageKind, extra: Extra = {}) =>
    ev(id, 'message.agent', { from_agent_id: from, from_label: `thread "${from}" (${from})`, kind, text: `${kind} ${id}`, ...extra }, { agent: to });

  it("adds rows for Desk's messages to threads and the user's steers and Asks, and hides start", () => {
    const events = [
      thread(1, 'a', 'Auth API'),
      fromDesk(2, 'a', 'start'),
      fromDesk(3, 'a', 'question', { tracked: true }),
      ev(4, 'message.user', { text: 'Use the v2 API.' }, { agent: 'a' }),
      ev(5, 'message.user', { text: 'How did you price it?', question: true }, { agent: 'a' }),
      ev(6, 'message.agent', { from_agent_id: 'a', from_label: 'thread "Auth API" (a)', kind: 'answer', text: 'JWT.', reply_to: 3 }, d),
      fromDesk(7, 'a', 'answer', { reply_to: 12 }),
      ev(8, 'message.agent', { from_agent_id: 'a', from_label: 'thread "Auth API" (a)', kind: 'answer', text: '(Auth API was stopped before answering.)', reply_to: 9, auto: true }, d),
    ];
    const s = events.reduce(reduceChat, emptyChat('d'));
    expect(s.items).toEqual([
      { kind: 'message', id: 'e:3', ts: ts(3), eventId: 3, from: 'd', to: 'a', messageKind: 'question', text: 'question 3' },
      { kind: 'steer', id: 'e:4', ts: ts(4), eventId: 4, to: 'a', text: 'Use the v2 API.' },
      { kind: 'steer', id: 'e:5', ts: ts(5), eventId: 5, to: 'a', text: 'How did you price it?', question: true },
      { kind: 'agent', id: 'e:6', ts: ts(6), fromAgentId: 'a', fromLabel: 'thread "Auth API" (a)', messageKind: 'answer', text: 'JWT.', replyTo: 3 },
      { kind: 'message', id: 'e:7', ts: ts(7), eventId: 7, from: 'd', to: 'a', messageKind: 'answer', text: 'answer 7', replyTo: 12 },
      {
        kind: 'agent',
        id: 'e:8',
        ts: ts(8),
        fromAgentId: 'a',
        fromLabel: 'thread "Auth API" (a)',
        messageKind: 'answer',
        text: '(Auth API was stopped before answering.)',
        replyTo: 9,
        auto: true,
      },
    ]);
  });

  it("leaves Desk's question open when the user steers a thread", () => {
    const asked = reduceChat(emptyChat('d'), ev(1, 'question.asked', { question: 'Ship on Friday?' }, d));
    expect(reduceChat(asked, ev(2, 'message.user', { text: 'Use the v2 API.' }, { agent: 'a' })).items[0]).toMatchObject({ kind: 'question', answered: false });
  });

  it('collects the messages between threads into one digest per Desk turn', () => {
    const events = [
      thread(1, 'a', 'Auth API'),
      thread(2, 'f', 'Frontend'),
      ev(3, 'run.started', { run_id: 'r1', model: 'm' }, d),
      between(4, 'a', 'f', 'question', { tracked: true }),
      between(5, 'f', 'a', 'answer', { reply_to: 4 }),
      // A thread's run does not freeze the digest; Desk's next run does.
      ev(6, 'run.started', { run_id: 'r2', model: 'm' }, { agent: 'f' }),
      between(7, 'a', 'f', 'note'),
      ev(8, 'run.started', { run_id: 'r3', model: 'm' }, d),
      between(9, 'f', 'a', 'note'),
    ];
    const s = events.reduce(reduceChat, emptyChat('d'));
    expect(s.items).toEqual([
      { kind: 'digest', id: 'e:4', ts: ts(4), eventId: 4, messageIds: [4, 5, 7] },
      { kind: 'digest', id: 'e:9', ts: ts(9), eventId: 9, messageIds: [9] },
    ]);
    expect(s.digest).toBe('e:9');
  });
});
