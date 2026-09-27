import { describe, expect, it } from 'vitest';
import { ev } from '@desk/client/testing';
import { runDetail, stepRun } from './testing/automations';
import { agentActivity, agentTokens, askDeskText, canStopStep, firedEdges, focusStep, outputText, relRunPath, runGraph, stepLook } from './automation-run-graph';

const now = Date.parse('2026-09-28T06:06:12.000Z');

describe('run looks', () => {
  it('words each step state for its node', () => {
    expect(stepLook(stepRun('a', { status: 'succeeded', route: 'changed', started_at: '2026-09-28T06:00:00.000Z', finished_at: '2026-09-28T06:00:12.000Z' }), now)).toEqual({ tone: 'ok', badge: '✓ 12s', detail: 'route changed' });
    expect(stepLook(stepRun('a', { status: 'running', started_at: '2026-09-28T06:00:12.000Z' }), now, 'web_fetch · acme.com')).toEqual({ tone: 'run', badge: '● 6m', detail: 'web_fetch · acme.com' });
    expect(stepLook(stepRun('a', { status: 'waiting', question: { text: 'OK?', files: [] } }), now)).toEqual({ tone: 'wait', badge: 'waiting', detail: 'waiting on you' });
    expect(stepLook(stepRun('a', { status: 'waiting', gate: { tool: 'skill_run', subject: 'digest/fetch.py', reason: 'r' } }), now).detail).toBe('approve skill_run');
    expect(stepLook(stepRun('a', { status: 'failed', error: 'boom\nstack' }), now)).toEqual({ tone: 'fail', badge: '✕ failed', detail: 'boom' });
    expect(stepLook(stepRun('a', { status: 'skipped' }), now).tone).toBe('skipped');
    expect(stepLook(stepRun('a', { attempt: 0 }), now)).toEqual({ tone: 'pending', badge: 'pending', detail: null });
    expect(stepLook(undefined, now).tone).toBe('pending');
  });

  it('lights the edges that fired, and the run as a whole', () => {
    const run = runDetail();
    expect([...firedEdges(run.definition, run.steps)]).toEqual([0]);
    const g = runGraph(run, now, { sum: 'reading acme.md' });
    expect(g.steps.fetch).toMatchObject({ tone: 'ok' });
    expect(g.steps.sum).toMatchObject({ tone: 'run', detail: 'reading acme.md' });
    expect(g.steps.ok).toMatchObject({ tone: 'pending' });
  });

  it('opens the step that needs the user first, then a running one, then a failed one', () => {
    expect(focusStep(runDetail())).toBe('sum');
    const waiting = runDetail({ steps: [...runDetail().steps.slice(0, 2), stepRun('ok', { status: 'waiting', question: { text: 'OK?', files: [] } })] });
    expect(focusStep(waiting)).toBe('ok');
    expect(focusStep(runDetail({ status: 'succeeded', steps: [stepRun('fetch', { status: 'succeeded' })] }))).toBeNull();
  });

  it('reads an agent’s current tool call from the log', () => {
    const events = [
      ev(1, 'tool.call', { run_id: 'x', tool_call_id: 't1', name: 'web_fetch', arguments: '{"url":"https://acme.com"}' }, { agent: 'ag1' }),
      ev(2, 'tool.result', { run_id: 'x', tool_call_id: 't1', name: 'web_fetch', status: 'ok', content: 'ok' }, { agent: 'ag1' }),
      ev(3, 'tool.call', { run_id: 'x', tool_call_id: 't2', name: 'read_file', arguments: '{"path":"acme.md"}' }, { agent: 'ag1' }),
    ];
    expect(agentActivity(events, 'ag1')).toMatch(/^read_file · /);
    expect(agentActivity(events.slice(0, 2), 'ag1')).toBeNull();
    expect(agentActivity(events, 'other')).toBeNull();
  });

  it('words the fix request, knows what can be stopped, and relativises question files', () => {
    const run = runDetail({ status: 'failed' });
    expect(askDeskText(run, 'Fetch pages', 'exit 1: timeout\nTraceback…')).toBe('Please fix digest: run #14 failed at Fetch pages. exit 1: timeout');
    const live = runDetail();
    expect(canStopStep(live, live.steps[1]!)).toBe(true);
    expect(canStopStep(live, live.steps[0]!)).toBe(false);
    expect(relRunPath('/data/automation-runs/r14/steps/sum/digest.md', 'r14')).toBe('steps/sum/digest.md');
    expect(relRunPath('/elsewhere/x.md', 'r14')).toBeNull();
  });
});

describe('step results', () => {
  it('counts an agent’s tokens and words outputs', () => {
    const events = [
      ev(1, 'usage', { run_id: 'x', model: 'm', prompt_tokens: 1000, completion_tokens: 200, estimated: false }, { agent: 'ag1' }),
      ev(2, 'usage', { run_id: 'y', model: 'm', prompt_tokens: 250, completion_tokens: 50, estimated: false }, { agent: 'ag1' }),
      ev(3, 'usage', { run_id: 'z', model: 'm', prompt_tokens: 9, completion_tokens: 9, estimated: false }, { agent: 'other' }),
    ];
    expect(agentTokens(events, 'ag1')).toBe(1500);
    expect(outputText(['a.com', 'b.com'])).toBe('a.com\nb.com');
    expect(outputText(3)).toBe('3');
    expect(outputText(null)).toBe('—');
    expect(outputText(false)).toBe('false');
  });
});
