import { describe, expect, it } from 'vitest';
import { emptyTranscript, reduceTranscript } from '@desk/client';
import { ev } from '@desk/client/testing';
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
