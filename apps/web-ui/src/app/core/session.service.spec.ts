import { Component, computed, inject, input } from '@angular/core';
import { render, screen, waitFor } from '@testing-library/angular';
import { describe, expect, it } from 'vitest';
import { agentTitle, answeringOf, waitingOn, type ProjectOverview } from '@desk/client';
import { ev } from '@desk/client/testing';
import type { StoredEvent } from '@desk/protocol';
import { FakeDeskBridge } from '../testing/fake-bridge';
import { injectSession, SESSION_RELEASE_DELAY, SessionService } from './session.service';

const overview = (): ProjectOverview => ({
  project: { id: 'p', name: 'Launch', goal: 'g', instructions: '', settings: { desk_model: 'm', thread_model: 'm', fallback_model: null, desk_reasoning_effort: null, thread_reasoning_effort: null, max_concurrent_threads: 4, check_in: 'normal', autonomy: 'dispatch-freely', review_rounds: 2, policy: [] }, created_at: 't', updated_at: 't', archived_at: null },
  desk: { id: 'd', project_id: 'p', role: 'desk', status: 'idle', model: 'm', reasoning_effort: null, title: 'Desk', brief: null, workspace_path: '/w', parent_id: null, inbox_cursor: 0, review_round: 0, result_summary: null, result_artifacts: null, active_skills: [], git_source_id: null, git_branch: null, git_base: null, git_common_dir: null, archived_at: null, created_at: 't', updated_at: 't' },
  sources: [],
  plan: null,
  threads: [],
  approvals: [],
  last_seq: 2,
});

const providers = (bridge: FakeDeskBridge) => [...bridge.providers, { provide: SESSION_RELEASE_DELAY, useValue: 0 }];

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
    expect(bridge.calls.filter((c) => c.channel === 'broker.watch').map((c) => c.input)).toEqual([
      { projectId: 'p', afterSeq: 0 },
      { projectId: 'p', afterSeq: 2 },
    ]);
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
