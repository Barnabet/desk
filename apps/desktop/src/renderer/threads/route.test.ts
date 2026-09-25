import { describe, expect, it } from 'vitest';
import { emptyTranscript, foldMessages, reduceTranscript } from '@desk/client';
import { ev } from '@desk/client/testing';
import type { AgentMessageKind, EventOf, RunFinishReason, StoredEvent } from '@desk/protocol';
import { clock } from '../format';
import { narrate, routeLayout, stopsOf, stopText } from './route';

const at = (min: number) => new Date(Date.UTC(2026, 8, 24, 10, min)).toISOString();
const a = { agent: 't' };

function transcript() {
  return [
    ev(1, 'agent.created', { role: 'thread', model: 'opus', title: 'Emails', brief: 'Draft five emails', workspace_path: '/w', parent_id: 'd' }, { ...a, ts: at(18) }),
    ev(2, 'agent.status_changed', { status: 'running' }, { ...a, ts: at(18) }),
    ev(3, 'run.started', { run_id: 'r1', model: 'opus' }, { ...a, ts: at(19) }),
    ev(4, 'assistant.message', { run_id: 'r1', content: 'Reading the brief.', tool_calls: [] }, { ...a, ts: at(19) }),
    ev(5, 'tool.call', { run_id: 'r1', tool_call_id: 'c1', name: 'read_file', arguments: '{"path":"a.md"}' }, { ...a, ts: at(20) }),
    ev(6, 'tool.result', { run_id: 'r1', tool_call_id: 'c1', name: 'read_file', status: 'ok', content: 'x' }, { ...a, ts: at(20) }),
    ev(7, 'tool.call', { run_id: 'r1', tool_call_id: 'c2', name: 'read_file', arguments: '{"path":"b.md"}' }, { ...a, ts: at(21) }),
    ev(8, 'tool.result', { run_id: 'r1', tool_call_id: 'c2', name: 'read_file', status: 'ok', content: 'y' }, { ...a, ts: at(21) }),
    ev(9, 'agent.model_switched', { from: 'opus', to: 'fable', reason: 'rate', scope: 'run' }, { ...a, ts: at(31) }),
    ev(10, 'context.compacted', { run_id: 'r1', checkpoint: 'earlier', up_to: 8, trigger: 'threshold' }, { ...a, ts: at(40) }),
    ev(11, 'agent.result', { summary: 'Five drafts published', artifacts: ['emails/01.md'] }, { ...a, ts: at(52) }),
    ev(12, 'agent.revision', { round: 1, feedback: 'Emails 3 and 4 read as salesy.' }, { ...a, ts: at(58) }),
    ev(13, 'message.user', { text: 'Keep email 5 under 120 words.' }, { ...a, ts: at(62) }),
    ev(14, 'tool.call', { run_id: 'r2', tool_call_id: 'c3', name: 'write_file', arguments: '{"path":"emails/04.md"}' }, { ...a, ts: at(63) }),
  ].reduce(reduceTranscript, emptyTranscript('t')).entries;
}

describe('narrate', () => {
  it('numbers stops, merges work, and keeps the compaction divider', () => {
    const rows = narrate(transcript());
    expect(rows.map((r) => (r.kind === 'stop' ? `${r.stop.n}:${r.stop.kind}` : 'compacted'))).toEqual([
      '1:brief',
      '2:work',
      '3:detour',
      'compacted',
      '4:result',
      '5:revision',
      '6:steer',
      '7:work',
    ]);
    const stops = stopsOf(rows);
    expect(stops[1]!.tools.map((c) => c.name)).toEqual(['read_file', 'read_file']);
    expect(stopText(stops[1]!, 2).sub).toMatch(/^read_file ×2 · /);
    expect(stops[6]!.live).toBe(true);
    expect(stopText(stops[6]!, 2).title).toBe('Using 1 tool');
    expect(stopText(stops[4]!, 2)).toMatchObject({ title: 'Desk sent it back', quote: 'Emails 3 and 4 read as salesy.' });
    expect(stopText(stops[2]!, 2).sub).toMatch(/^continued on fable/);
  });
});

describe('routeLayout', () => {
  it('spreads each row across the full width, with the turns inside it', () => {
    const stops = stopsOf(narrate(transcript()));
    const l = routeLayout(stops, 980, false);
    const xs = l.points.filter((p) => p.row === 0).map((p) => p.x);
    expect(Math.min(...xs)).toBe(120);
    expect(Math.max(...xs)).toBe(860);
    for (const piece of l.pieces) {
      for (const n of piece.d.match(/-?\d+(\.\d+)?/g)!.filter((_, i) => i % 2 === 0).map(Number)) {
        expect(n).toBeGreaterThanOrEqual(0);
        expect(n).toBeLessThanOrEqual(980);
      }
    }
  });

  it('snakes stops across rows and marks the live stretch after the last revision', () => {
    const stops = stopsOf(narrate(transcript()));
    const l = routeLayout(stops, 980, true);
    expect(l.points).toHaveLength(7);
    const rows = [...new Set(l.points.map((p) => p.row))];
    expect(rows.length).toBeGreaterThan(1);
    const row0 = l.points.filter((p) => p.row === 0 && p.stop.kind !== 'detour');
    const row1 = l.points.filter((p) => p.row === 1);
    expect(row0[1]!.x).toBeGreaterThan(row0[0]!.x);
    if (row1.length > 1) expect(row1[1]!.x).toBeLessThan(row1[0]!.x);
    expect(l.points.find((p) => p.stop.kind === 'detour')!.y).toBeLessThan(row0[0]!.y);
    expect(l.pieces).toHaveLength(7);
    expect(l.pieces.at(-1)!.live).toBe(true);
    expect(l.pieces[0]!.live).toBe(false);
    expect(l.now).not.toBeNull();
    expect(l.tailPath).not.toBeNull();
    for (const p of l.points) expect(p.x).toBeGreaterThan(0), expect(p.x).toBeLessThan(980);
    expect(l.height).toBeGreaterThan(Math.max(...l.points.map((p) => p.y)));
  });

  it('has no now marker for a finished thread', () => {
    const l = routeLayout(stopsOf(narrate(transcript())), 1400, false);
    expect(l.now).toBeNull();
    expect(l.tail).toEqual([]);
    expect(l.pieces.every((p) => !p.live)).toBe(true);
  });
});

describe('messages and answer runs on the route', () => {
  /** `ev()` stamps event `id` this many seconds after 10:00:00Z on 2026-09-24. */
  const ts = (id: number) => new Date(Date.UTC(2026, 8, 24, 10, 0, id)).toISOString();
  const t = { agent: 't' };
  const LABEL: Record<string, string> = { d: 'Desk', f: 'thread "Frontend" (f)', t: 'thread "Pricing" (t)' };
  type Extra = Partial<EventOf<'message.agent'>['payload']>;
  const agentMsg = (id: number, to: string, from: string, kind: AgentMessageKind, text: string, extra: Extra = {}) =>
    ev(id, 'message.agent', { from_agent_id: from, from_label: LABEL[from] ?? from, kind, text, ...extra }, { agent: to });
  /** Desk d, the asker f ("Frontend"), and the thread t ("Pricing"), which finished before it is asked. */
  const team = () => [
    ev(1, 'agent.created', { role: 'desk', model: 'm', title: 'Desk', brief: null, workspace_path: '/d', parent_id: null }, { agent: 'd' }),
    ev(2, 'agent.created', { role: 'thread', model: 'm', title: 'Frontend', brief: 'Build the page', workspace_path: '/f', parent_id: 'd' }, { agent: 'f' }),
    ev(3, 'agent.created', { role: 'thread', model: 'm', title: 'Pricing', brief: 'Price the plans', workspace_path: '/t', parent_id: 'd' }, t),
    ev(4, 'agent.status_changed', { status: 'done' }, t),
  ];
  const Q = 10;
  const question = (asker: 'f' | 'user') =>
    asker === 'user' ? ev(Q, 'message.user', { text: 'How did you price it?', question: true }, t) : agentMsg(Q, 't', 'f', 'question', 'Which currency?', { tracked: true });
  const start = () => ev(11, 'run.started', { run_id: 'r1', model: 'm', answering: Q }, t);
  const said = (id: number, content: string | null, tool?: string) =>
    ev(id, 'assistant.message', { run_id: 'r1', content, tool_calls: tool ? [{ id: `c${id}`, name: tool, arguments: '{}' }] : [] }, t);
  const call = (id: number, callId: string, tool: string) => ev(id, 'tool.call', { run_id: 'r1', tool_call_id: callId, name: tool, arguments: '{}' }, t);
  const result = (id: number, callId: string, tool: string) => ev(id, 'tool.result', { run_id: 'r1', tool_call_id: callId, name: tool, status: 'ok', content: 'ok' }, t);
  /** One step that calls `tool`, saying `content` next to it: the reply, the call and its result. */
  const step = (id: number, tool: string, content: string | null = null) => [said(id, content, tool), call(id + 1, `c${id}`, tool), result(id + 2, `c${id}`, tool)];
  const fourReads = (content: string | null = null) => [...step(12, 'read_file', content), ...step(15, 'read_file'), ...step(18, 'read_file'), ...step(21, 'read_file')];
  const end = (id: number, reason: RunFinishReason, detail?: string) => ev(id, 'run.finished', { run_id: 'r1', reason, ...(detail ? { detail } : {}) }, t);
  /** Pricing's answer, or the runtime's closure, on Frontend's stream. */
  const answer = (id: number, text: string, extra: Extra = {}) => agentMsg(id, 'f', 't', 'answer', text, { reply_to: Q, ...extra });
  const sub = clock(ts(11));

  /** The answer run's stop words, from a transcript and a fold of the same events. */
  function answerStop(asker: 'f' | 'user', run: StoredEvent[]) {
    const events = [...team(), question(asker), start(), ...run];
    const stop = stopsOf(narrate(events.reduce(reduceTranscript, emptyTranscript('t')).entries)).find((x) => x.kind === 'answer')!;
    return stopText(stop, 2, foldMessages(events));
  }

  it.each([
    { ending: 'a text answer', run: [said(12, 'Euros.'), answer(13, 'Euros.'), end(14, 'no_tool_calls')], title: 'Answered Frontend', sub, quote: 'Euros.' },
    {
      ending: 'a message answer',
      run: [said(12, null, 'message_thread'), call(13, 'c12', 'message_thread'), answer(14, 'Euros.', { tool_call_id: 'c12' }), result(15, 'c12', 'message_thread'), end(16, 'yielded')],
      title: 'Answered Frontend',
      sub,
      quote: 'Euros.',
    },
    { ending: 'empty text', run: [said(12, ''), answer(13, '(Pricing did not answer.)', { auto: true }), end(14, 'no_tool_calls')], title: "Couldn't answer Frontend", sub, quote: '(Pricing did not answer.)', muted: true },
    {
      ending: 'the step limit',
      run: [...fourReads(), answer(24, '(Pricing did not finish answering. Ask again or use read_thread.)', { auto: true }), end(25, 'max_steps', 'Reached the limit of 4 steps')],
      title: "Couldn't answer Frontend",
      sub,
      quote: '(Pricing did not finish answering. Ask again or use read_thread.)',
      muted: true,
    },
    { ending: 'a stop', run: [answer(12, '(Pricing was stopped before answering.)', { auto: true }), end(13, 'stopped')], title: "Couldn't answer Frontend", sub, quote: '(Pricing was stopped before answering.)', muted: true },
    {
      ending: 'a model error',
      run: [answer(12, '(Pricing could not answer: 401 bad key.)', { auto: true }), end(13, 'error', '401 bad key')],
      title: "Couldn't answer Frontend",
      sub,
      quote: '(Pricing could not answer: 401 bad key.)',
      muted: true,
    },
    { ending: 'empty text after the question was withdrawn', run: [ev(12, 'agent.status_changed', { status: 'done' }, { agent: 'f' }), said(13, ''), end(14, 'no_tool_calls')], title: "Couldn't answer Frontend", sub, muted: true },
  ])("titles the answer run for another agent's question on $ending", ({ ending: _ending, run, ...want }) => {
    expect(answerStop('f', run)).toEqual(want);
  });

  it.each([
    { ending: 'a text answer', run: [said(12, 'Per seat.'), end(13, 'no_tool_calls')], title: 'Answered you', sub, quote: 'Per seat.' },
    { ending: 'empty text', run: [said(12, ''), end(13, 'no_tool_calls')], title: "Couldn't answer you", sub: `no answer · ${sub}`, quote: 'How did you price it?', muted: true },
    { ending: 'the step limit', run: [...fourReads(), end(24, 'max_steps', 'Reached the limit of 4 steps')], title: "Couldn't answer you", sub: `step limit · ${sub}`, quote: 'How did you price it?', muted: true },
    { ending: 'a stop', run: [end(12, 'stopped')], title: "Couldn't answer you", sub: `stopped · ${sub}`, quote: 'How did you price it?', muted: true },
    { ending: 'a model error', run: [end(12, 'error', '401 bad key')], title: "Couldn't answer you", sub: `401 bad key · ${sub}`, quote: 'How did you price it?', muted: true },
    { ending: 'a restart', run: [end(12, 'error', 'daemon_shutdown')], title: "Couldn't answer you", sub: `Desk was restarting · ${sub}`, quote: 'How did you price it?', muted: true },
  ])("titles the answer run for the user's Ask on $ending", ({ ending: _ending, run, ...want }) => {
    expect(answerStop('user', run)).toEqual(want);
  });

  it("does not count text written next to a tool call as the answer to the user's Ask", () => {
    const run = [...fourReads('Let me check the file'), end(24, 'max_steps', 'Reached the limit of 4 steps')];
    expect(answerStop('user', run)).toEqual({ title: "Couldn't answer you", sub: `step limit · ${sub}`, quote: 'How did you price it?', muted: true });
  });

  it('reads "Answering" while the answer run is live', () => {
    expect(answerStop('f', [])).toEqual({ title: 'Answering Frontend', sub, quote: 'Which currency?' });
    expect(answerStop('user', step(12, 'read_file', 'Let me check the file'))).toEqual({ title: 'Answering you', sub, quote: 'How did you price it?' });
  });

  it('narrates an answer run as one stop that a message does not split, until the next status change', () => {
    const events = [
      ...team(),
      question('f'),
      start(),
      ...step(12, 'read_file'),
      agentMsg(15, 't', 'd', 'note', 'EUR only.'),
      said(16, 'Euros.'),
      answer(17, 'Euros.'),
      end(18, 'no_tool_calls'),
      ev(19, 'agent.status_changed', { status: 'queued' }, t),
      ev(20, 'assistant.message', { run_id: 'r2', content: 'Back to work.', tool_calls: [] }, t),
    ];
    const rows = narrate(events.reduce(reduceTranscript, emptyTranscript('t')).entries);
    expect(rows.map((r) => (r.kind === 'stop' ? `${r.stop.n}:${r.stop.kind}` : r.kind))).toEqual(['1:brief', '2:incoming', '3:answer', '4:incoming', '5:work']);
    const run = stopsOf(rows)[2]!;
    expect(run.entries.map((e) => e.kind)).toEqual(['answer', 'tools', 'assistant']);
    expect(run.tools.map((c) => c.name)).toEqual(['read_file']);
    expect(run.live).toBe(false);
    const live = stopsOf(narrate([...team(), question('f'), start(), ...step(12, 'read_file')].reduce(reduceTranscript, emptyTranscript('t')).entries));
    expect(live.at(-1)).toMatchObject({ kind: 'answer', live: true });
  });

  it("titles messages by their sender from the fold's directory, and the user's messages as steers or Asks", () => {
    const events = [
      ...team(),
      agentMsg(20, 't', 'd', 'start', 'Begin your assignment.'),
      agentMsg(21, 't', 'd', 'note', 'Use EUR.'),
      agentMsg(22, 't', 'd', 'question', 'Done?', { tracked: true }),
      agentMsg(23, 't', 'f', 'question', 'Which currency?', { tracked: true }),
      agentMsg(24, 't', 'f', 'note', 'Renamed price_eur.'),
      agentMsg(25, 't', 'f', 'answer', 'EUR.', { reply_to: 9 }),
      agentMsg(26, 't', 'f', 'answer', '(Frontend was stopped before answering.)', { reply_to: 8, auto: true }),
      // Archived threads keep their titles in the fold.
      ev(27, 'agent.archived', {}, { agent: 'f' }),
      ev(28, 'message.user', { text: 'Use the v2 API.' }, t),
      ev(29, 'message.user', { text: 'How did you price it?', question: true }, t),
      // A sender the fold does not know is named from its label.
      agentMsg(30, 't', 'x', 'update', 'Legacy.', { from_label: 'thread "Old stream" (x)' }),
    ];
    const m = foldMessages(events);
    const stops = stopsOf(narrate(events.reduce(reduceTranscript, emptyTranscript('t')).entries));
    expect(stops.map((x) => [x.kind, stopText(x, 2, m).title, stopText(x, 2, m).muted ?? false])).toEqual([
      ['brief', 'Brief from Desk', false],
      ['incoming', 'Desk: note', false],
      ['incoming', 'Desk asked', false],
      ['incoming', 'Frontend asked', false],
      ['incoming', 'Frontend: note', false],
      ['incoming', 'Answer from Frontend', false],
      ['incoming', 'Frontend could not answer', true],
      ['steer', `You steered · ${clock(ts(28))}`, false],
      ['steer', `You asked · ${clock(ts(29))}`, false],
      ['incoming', 'Old stream: update', false],
    ]);
  });
});
