import { Component, computed, inject, input } from '@angular/core';
import { render, screen, waitFor } from '@testing-library/angular';
import { describe, expect, it } from 'vitest';
import { initialGlobalState, type ConnectionStatus } from '@desk/bff/contract';
import { agentTitle, answeringOf, waitingOn, type ProjectOverview } from '@desk/client';
import { ev } from '@desk/client/testing';
import type { StoredEvent } from '@desk/protocol';
import { FakeDeskBridge } from '../testing/fake-bridge';
import { injectSession, SESSION_RELEASE_DELAY, SessionService } from './session.service';

const overview = (): ProjectOverview => ({
  project: { id: 'p', name: 'Launch', goal: 'g', instructions: '', settings: { desk_model: 'm', thread_model: 'm', fallback_model: null, desk_reasoning_effort: null, thread_reasoning_effort: null, max_concurrent_threads: 4, check_in: 'normal', autonomy: 'dispatch-freely', review_rounds: 2, review_model: null, policy: [] }, created_at: 't', updated_at: 't', archived_at: null },
  desk: { id: 'd', project_id: 'p', role: 'desk', status: 'idle', model: 'm', reasoning_effort: null, title: 'Desk', brief: null, workspace_path: '/w', parent_id: null, inbox_cursor: 0, review_round: 0, acceptance: 'none', accepted_submission_id: null, reviews_submission_id: null, result_summary: null, result_artifacts: null, active_skills: [], git_source_id: null, git_branch: null, git_base: null, git_common_dir: null, automation_run_id: null, automation_step_id: null, archived_at: null, created_at: 't', updated_at: 't' },
  sources: [],
  plan: null,
  threads: [],
  approvals: [],
  last_seq: 2,
});

const providers = (bridge: FakeDeskBridge, releaseDelay = 0) => [...bridge.providers, { provide: SESSION_RELEASE_DELAY, useValue: releaseDelay }];
const watches = (bridge: FakeDeskBridge) => bridge.calls.filter((c) => c.channel === 'broker.watch').map((c) => c.input);
const connection = (bridge: FakeDeskBridge, status: ConnectionStatus) => bridge.emit('desk:global', { ...initialGlobalState(), connection: { status } });

/** The desktop test's Probe: status | chat kinds | lanes | threads | the agent's transcript | Desk's live text. */
@Component({ selector: 'desk-probe', template: '<p data-testid="probe">{{ line() }}</p>' })
class Probe {
  readonly id = input.required<string>();
  readonly agent = input.required<string>();
  private readonly sessions = inject(SessionService);
  private readonly s = injectSession(this.id);
  protected readonly line = computed(() => {
    const s = this.s();
    const t = this.sessions.transcript(this.id(), this.agent())();
    return [s.status, s.chat.items.map((i) => i.kind).join(','), s.timeline.lanes.length, s.project?.threads.length ?? '-', t.entries.map((e) => e.kind).join(','), s.streams['d']?.text ?? ''].join('|');
  });
}

describe('project session', () => {
  it('loads, watches from zero, folds events and streams, then unwatches', async () => {
    const events = [
      ev(1, 'project.created', { name: 'Launch', goal: 'g', instructions: '' }),
      ev(2, 'agent.created', { role: 'desk', model: 'm', title: 'Desk', brief: null, workspace_path: '/w', parent_id: null }, { agent: 'd' }),
      ev(3, 'message.user', { text: 'Kick off' }, { agent: 'd' }),
      ev(4, 'agent.created', { role: 'thread', model: 'm', title: 'Checklist', brief: 'Build it', workspace_path: '/w/t', parent_id: 'd' }, { agent: 't' }),
      ev(5, 'agent.status_changed', { status: 'running' }, { agent: 't' }),
    ];
    const bridge: FakeDeskBridge = new FakeDeskBridge({
      'projects.get': () => overview(),
      'broker.watch': () => {
        for (const e of events) bridge.emit('desk:event', e);
        return { ok: true };
      },
      'broker.unwatch': () => ({ ok: true }),
    });
    const view = await render(Probe, { inputs: { id: 'p', agent: 't' }, providers: providers(bridge) });
    await waitFor(() => expect(screen.getByTestId('probe').textContent).toBe('ready|user|1|1|brief,status|'));
    expect(bridge.calls.find((c) => c.channel === 'broker.watch')?.input).toEqual({ projectId: 'p', afterSeq: 0 });

    bridge.emit('desk:ephemeral', { type: 'assistant.delta', project_id: 'p', agent_id: 'd', payload: { run_id: 'r', text: 'Hel' } });
    bridge.emit('desk:ephemeral', { type: 'assistant.delta', project_id: 'p', agent_id: 'd', payload: { run_id: 'r', text: 'lo' } });
    await waitFor(() => expect(screen.getByTestId('probe').textContent).toBe('ready|user,assistant|1|1|brief,status|Hello'));
    bridge.emit('desk:event', ev(6, 'assistant.message', { run_id: 'r', content: 'Hello there', tool_calls: [] }, { agent: 'd' }));
    bridge.emit('desk:event', ev(6, 'assistant.message', { run_id: 'r', content: 'Hello there', tool_calls: [] }, { agent: 'd' }));
    await waitFor(() => expect(screen.getByTestId('probe').textContent).toBe('ready|user,assistant|1|1|brief,status|'));

    view.fixture.destroy();
    await waitFor(() => expect(bridge.calls.some((c) => c.channel === 'broker.unwatch')).toBe(true));
  });

  it('reports a missing project', async () => {
    const bridge = new FakeDeskBridge({ 'projects.get': () => Promise.reject({ code: 'not_found', message: 'Unknown project: x', status: 404 }) });
    await render(Probe, { inputs: { id: 'x', agent: 't' }, providers: providers(bridge) });
    await waitFor(() => expect(screen.getByTestId('probe').textContent?.startsWith('missing|')).toBe(true));
  });

  it('watches again from its last event when /push reconnects', async () => {
    const events: StoredEvent[] = [
      ev(1, 'project.created', { name: 'Launch', goal: 'g', instructions: '' }),
      ev(2, 'agent.created', { role: 'desk', model: 'm', title: 'Desk', brief: null, workspace_path: '/w', parent_id: null }, { agent: 'd' }),
    ];
    const bridge: FakeDeskBridge = new FakeDeskBridge({
      'projects.get': () => overview(),
      'broker.watch': ({ afterSeq }: { afterSeq: number }) => {
        for (const e of events) if (e.id > afterSeq) bridge.emit('desk:event', e);
        return { ok: true };
      },
    });
    await render(Probe, { inputs: { id: 'p', agent: 't' }, providers: providers(bridge) });
    await waitFor(() => expect(screen.getByTestId('probe').textContent?.startsWith('ready|')).toBe(true));
    events.push(ev(3, 'message.user', { text: 'Kick off' }, { agent: 'd' }));
    bridge.reconnect();
    await waitFor(() => expect(screen.getByTestId('probe').textContent?.split('|')[1]).toBe('user'));
    expect(watches(bridge)).toEqual([
      { projectId: 'p', afterSeq: 0 },
      { projectId: 'p', afterSeq: 2 },
    ]);
  });

  it('moves its first watch on with what arrives, so a watch sent again after a drop resumes rather than starting over', async () => {
    const events: StoredEvent[] = [
      ev(1, 'project.created', { name: 'Launch', goal: 'g', instructions: '' }),
      ev(2, 'agent.created', { role: 'desk', model: 'm', title: 'Desk', brief: null, workspace_path: '/w', parent_id: null }, { agent: 'd' }),
      ev(3, 'message.user', { text: 'Kick off' }, { agent: 'd' }),
    ];
    const resumed: number[] = [];
    const bridge: FakeDeskBridge = new FakeDeskBridge({
      'projects.get': () => overview(),
      // The socket drops after the first frame; the bridge sends the same input again on the next one.
      'broker.watch': (input: { afterSeq: number }) => {
        bridge.emit('desk:events', events.slice(0, 2));
        resumed.push(input.afterSeq);
        bridge.emit('desk:events', events.filter((e) => e.id > input.afterSeq));
        // A frame sent twice is dropped.
        bridge.emit('desk:events', events.slice(0, 2));
        return { ok: true };
      },
    });
    await render(Probe, { inputs: { id: 'p', agent: 'd' }, providers: providers(bridge) });
    await waitFor(() => expect(screen.getByTestId('probe').textContent).toBe('ready|user|0|0|steer|'));
    expect(resumed).toEqual([2]);
  });

  it("on a phone, fetches a thread's history when its transcript is shown, and catches up after a reconnect", async () => {
    const desk = ev(2, 'agent.created', { role: 'desk', model: 'm', title: 'Desk', brief: null, workspace_path: '/w', parent_id: null }, { agent: 'd' });
    const thread = ev(3, 'agent.created', { role: 'thread', model: 'm', title: 'Checklist', brief: 'Build it', workspace_path: '/w/t', parent_id: 'd' }, { agent: 't' });
    const said = (id: number, content: string) => ev(id, 'assistant.message', { run_id: 'r', content, tool_calls: [] }, { agent: 't' });
    // What desk web sends a phone: the threads' transcripts left out of the backfill.
    const backfill: StoredEvent[] = [ev(1, 'project.created', { name: 'Launch', goal: 'g', instructions: '' }), desk, thread, ev(5, 'message.user', { text: 'Kick off' }, { agent: 'd' })];
    const history: StoredEvent[] = [thread, said(4, 'Reading the brief.')];
    const asked: Array<{ id: string; after?: number }> = [];
    const bridge: FakeDeskBridge = new FakeDeskBridge({
      'projects.get': () => overview(),
      'broker.watch': ({ afterSeq }: { afterSeq: number }) => {
        bridge.emit('desk:events', backfill.filter((e) => e.id > afterSeq));
        return { ok: true };
      },
      'threads.transcript': (i: { id: string; after?: number }) => {
        asked.push({ id: i.id, after: i.after });
        const events = history.filter((e) => e.agent_id === i.id && e.id > (i.after ?? 0));
        return { events, next_after: events.at(-1)?.id ?? i.after ?? 0 };
      },
    });
    bridge.remote = true;
    await render(Probe, { inputs: { id: 'p', agent: 't' }, providers: providers(bridge) });
    await waitFor(() => expect(screen.getByTestId('probe').textContent).toBe('ready|user|1|1|brief,assistant|'));
    expect(asked).toEqual([{ id: 't', after: 0 }]);
    // Said while the phone was away: the backfill after the drop leaves it out, the fetch after the rewatch brings it.
    history.push(said(6, 'Done.'));
    backfill.push(ev(7, 'message.user', { text: 'Thanks' }, { agent: 'd' }));
    bridge.reconnect();
    await waitFor(() => expect(screen.getByTestId('probe').textContent).toBe('ready|user,user|1|1|brief,assistant,assistant|'));
    expect(asked).toEqual([
      { id: 't', after: 0 },
      { id: 't', after: 5 },
    ]);
  });

  it('never fetches histories off a phone, nor Desk\'s own on a phone', async () => {
    const bridge: FakeDeskBridge = new FakeDeskBridge({
      'projects.get': () => overview(),
      'broker.watch': () => ({ ok: true }),
      'threads.transcript': () => ({ events: [], next_after: 0 }),
    });
    await render(Probe, { inputs: { id: 'p', agent: 't' }, providers: providers(bridge) });
    await waitFor(() => expect(screen.getByTestId('probe').textContent?.startsWith('ready|')).toBe(true));
    expect(bridge.calls.filter((c) => c.channel === 'threads.transcript')).toEqual([]);
  });

  it('watches again when a watch after a reconnect failed: on the next reconnect, and once deskd is live again', async () => {
    const events: StoredEvent[] = [ev(1, 'project.created', { name: 'Launch', goal: 'g', instructions: '' })];
    let deskd = true;
    const bridge: FakeDeskBridge = new FakeDeskBridge({
      'projects.get': () => overview(),
      'broker.watch': ({ afterSeq }: { afterSeq: number }) => {
        if (!deskd) throw { code: 'daemon_not_running', message: 'deskd is not running' };
        for (const e of events) if (e.id > afterSeq) bridge.emit('desk:event', e);
        return { ok: true };
      },
    });
    await render(Probe, { inputs: { id: 'p', agent: 't' }, providers: providers(bridge) });
    await waitFor(() => expect(screen.getByTestId('probe').textContent?.startsWith('ready|')).toBe(true));
    // /push comes back while deskd is down: the broker cannot register the watch.
    deskd = false;
    connection(bridge, 'offline');
    bridge.reconnect();
    await waitFor(() => expect(watches(bridge)).toHaveLength(2));
    bridge.reconnect();
    await waitFor(() => expect(watches(bridge)).toHaveLength(3));
    deskd = true;
    events.push(ev(2, 'message.user', { text: 'Kick off' }, { agent: 'd' }));
    connection(bridge, 'connecting');
    await Promise.resolve();
    expect(watches(bridge)).toHaveLength(3);
    connection(bridge, 'live');
    await waitFor(() => expect(screen.getByTestId('probe').textContent?.split('|')[1]).toBe('user'));
    expect(watches(bridge)).toEqual([
      { projectId: 'p', afterSeq: 0 },
      { projectId: 'p', afterSeq: 1 },
      { projectId: 'p', afterSeq: 1 },
      { projectId: 'p', afterSeq: 1 },
    ]);
    // That watch held: later pushes of a live deskd watch nothing again.
    connection(bridge, 'offline');
    connection(bridge, 'live');
    await Promise.resolve();
    expect(watches(bridge)).toHaveLength(4);
  });

  it('follows the project id: releases the old session and loads the new one', async () => {
    const qThread = { ...overview().desk!, id: 'qt', role: 'thread' as const, title: 'Q work', parent_id: 'd' };
    const bridge = new FakeDeskBridge({
      'projects.get': ({ id }: { id: string }) => (id === 'q' ? { ...overview(), threads: [qThread] } : overview()),
      'broker.watch': () => ({ ok: true }),
      'broker.unwatch': () => ({ ok: true }),
    });
    const view = await render(Probe, { inputs: { id: 'p', agent: 't' }, providers: providers(bridge) });
    await waitFor(() => expect(screen.getByTestId('probe').textContent).toBe('ready||0|0||'));
    view.fixture.componentRef.setInput('id', 'q');
    // The signal follows the id: q's overview has one thread.
    await waitFor(() => expect(screen.getByTestId('probe').textContent).toBe('ready||0|1||'));
    await waitFor(() => expect(bridge.calls.some((c) => c.channel === 'broker.unwatch')).toBe(true));
    const calls = bridge.calls.map((c) => [c.channel, c.input]);
    expect(calls.slice(0, 2)).toEqual([
      ['projects.get', { id: 'p' }],
      ['broker.watch', { projectId: 'p', afterSeq: 0 }],
    ]);
    expect(calls.slice(2)).toHaveLength(3);
    expect(calls.slice(2)).toEqual(
      expect.arrayContaining([
        ['projects.get', { id: 'q' }],
        ['broker.watch', { projectId: 'q', afterSeq: 0 }],
        ['broker.unwatch', { projectId: 'p' }],
      ]),
    );
  });

  it('watches a warm session again when /push reconnects (its last viewer gone, the release still pending)', async () => {
    const bridge: FakeDeskBridge = new FakeDeskBridge({
      'projects.get': () => overview(),
      'broker.watch': () => {
        bridge.emit('desk:event', ev(1, 'project.created', { name: 'Launch', goal: 'g', instructions: '' }));
        return { ok: true };
      },
      'broker.unwatch': () => ({ ok: true }),
    });
    const view = await render(Probe, { inputs: { id: 'p', agent: 't' }, providers: providers(bridge, 60_000) });
    await waitFor(() => expect(screen.getByTestId('probe').textContent?.startsWith('ready|')).toBe(true));
    view.fixture.destroy();
    bridge.reconnect();
    await waitFor(() => expect(watches(bridge)).toEqual([
      { projectId: 'p', afterSeq: 0 },
      { projectId: 'p', afterSeq: 1 },
    ]));
    expect(bridge.calls.some((c) => c.channel === 'broker.unwatch')).toBe(false);
  });
});

@Component({ selector: 'desk-waits', template: '<p data-testid="waits">{{ line() }}</p>' })
class Waits {
  readonly id = input.required<string>();
  private readonly s = injectSession(this.id);
  protected readonly line = computed(() => {
    const s = this.s();
    const run = answeringOf(s.messages, 'f');
    const waits = waitingOn(s.messages, 'a', []).map((w) => `${agentTitle(s.messages, w.agentId)} since ${w.since}`);
    const threads = s.project?.threads.map((t) => `${t.id}:${t.status}`).join(',') ?? '-';
    return [s.status, threads, run ? `answering ${run.asker} #${run.question}` : 'not answering', waits.join(',') || 'waiting on nothing'].join('|');
  });
}

describe("the session's message fold", () => {
  it('shows answering and waiting-on from the backfill alone, with the overview past every event', async () => {
    const at = (id: number) => new Date(Date.UTC(2026, 8, 24, 10, 0, id)).toISOString();
    const thread = (id: string, title: string, status: 'waiting' | 'done') => ({ ...overview().desk!, id, role: 'thread' as const, title, status, parent_id: 'd' });
    const events = [
      ev(1, 'project.created', { name: 'Launch', goal: 'g', instructions: '' }),
      ev(2, 'agent.created', { role: 'desk', model: 'm', title: 'Desk', brief: null, workspace_path: '/w', parent_id: null }, { agent: 'd' }),
      ev(3, 'agent.created', { role: 'thread', model: 'm', title: 'Auth API', brief: 'b', workspace_path: '/w/a', parent_id: 'd' }, { agent: 'a' }),
      ev(4, 'agent.created', { role: 'thread', model: 'm', title: 'Frontend', brief: 'b', workspace_path: '/w/f', parent_id: 'd' }, { agent: 'f' }),
      ev(5, 'agent.status_changed', { status: 'done' }, { agent: 'f' }),
      ev(6, 'message.agent', { from_agent_id: 'a', from_label: 'thread "Auth API" (a)', kind: 'question', text: 'Which token format?', tracked: true }, { agent: 'f' }),
      ev(7, 'agent.status_changed', { status: 'waiting', reason: 'Waiting on "Frontend"' }, { agent: 'a' }),
      ev(8, 'run.started', { run_id: 'r1', model: 'm', answering: 6 }, { agent: 'f' }),
    ];
    const bridge: FakeDeskBridge = new FakeDeskBridge({
      // A reload: the overview already includes every event (last_seq 8), so reduceProject ignores the whole backfill.
      'projects.get': () => ({ ...overview(), threads: [thread('a', 'Auth API', 'waiting'), thread('f', 'Frontend', 'done')], last_seq: 8 }),
      'broker.watch': () => {
        for (const e of events) bridge.emit('desk:event', e);
        return { ok: true };
      },
      'broker.unwatch': () => ({ ok: true }),
    });
    await render(Waits, { inputs: { id: 'p' }, providers: providers(bridge) });
    await waitFor(() => expect(screen.getByTestId('waits').textContent).toBe(`ready|a:waiting,f:done|answering a #6|Frontend since ${at(6)}`));
  });
});
