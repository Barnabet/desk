import { afterEach, beforeEach, describe, expect, it } from 'vitest';
import { sql } from 'drizzle-orm';
import type { EventInput } from '@desk/protocol';
import { openDb } from '../db/open';
import { getAgent, getProject, getUsageTotals, listAgents } from '../state/queries';
import { EventStore, type StreamItem } from './store';

let close: () => void;
let store: EventStore;

beforeEach(() => {
  const opened = openDb(':memory:');
  close = opened.close;
  store = new EventStore(opened.db, () => new Date('2026-09-23T10:00:00.000Z'));
});
afterEach(() => close());

const project = (): EventInput => ({
  project_id: 'p1',
  agent_id: null,
  type: 'project.created',
  payload: { name: 'Demo', goal: 'Ship it', instructions: '' },
});
const agent = (id = 'a1'): EventInput => ({
  project_id: 'p1',
  agent_id: id,
  type: 'agent.created',
  payload: { role: 'thread', model: 'claude-opus-5-5', title: 'T', brief: 'B', workspace_path: '/tmp/w', parent_id: null },
});

describe('EventStore', () => {
  it('assigns increasing ids and timestamps', () => {
    const [a, b] = store.append([project(), agent()]);
    expect(a!.id).toBeLessThan(b!.id);
    expect(a!.ts).toBe('2026-09-23T10:00:00.000Z');
  });

  it('lists events filtered by agent, type and cursor', () => {
    const [, created] = store.append([project(), agent()]);
    store.append({ project_id: 'p1', agent_id: 'a1', type: 'message.user', payload: { text: 'hi' } });
    expect(store.list({ agentId: 'a1' }).map((e) => e.type)).toEqual(['agent.created', 'message.user']);
    expect(store.list({ types: ['message.user'] })).toHaveLength(1);
    expect(store.list({ after: created!.id }).map((e) => e.type)).toEqual(['message.user']);
    expect(store.list({ limit: 1 })).toHaveLength(1);
  });

  it('projects projects and agents', () => {
    store.append([project(), agent()]);
    expect(getProject(store.db, 'p1')?.name).toBe('Demo');
    const a = getAgent(store.db, 'a1');
    expect(a).toMatchObject({ role: 'thread', status: 'idle', inbox_cursor: 0, workspace_path: '/tmp/w' });
    expect(listAgents(store.db, 'p1')).toHaveLength(1);
  });

  it('projects status changes and inbox cursor', () => {
    store.append([project(), agent()]);
    store.append({ project_id: 'p1', agent_id: 'a1', type: 'agent.status_changed', payload: { status: 'running' } });
    store.append({ project_id: 'p1', agent_id: 'a1', type: 'inbox.drained', payload: { run_id: 'r', up_to: 42 } });
    expect(getAgent(store.db, 'a1')).toMatchObject({ status: 'running', inbox_cursor: 42 });
  });

  it('aggregates usage per day', () => {
    store.append([project(), agent()]);
    const usage = (p: number, c: number): EventInput => ({
      project_id: 'p1',
      agent_id: 'a1',
      type: 'usage',
      payload: { run_id: 'r', model: 'claude-opus-5-5', prompt_tokens: p, completion_tokens: c, estimated: false },
    });
    store.append([usage(100, 10), usage(50, 5)]);
    expect(getUsageTotals(store.db, 'p1')).toEqual([
      { project_id: 'p1', agent_id: 'a1', model: 'claude-opus-5-5', day: '2026-09-23', prompt_tokens: 150, completion_tokens: 15 },
    ]);
  });

  it('rejects invalid payloads and rolls back the whole batch', () => {
    expect(() =>
      store.append([project(), { project_id: 'p1', agent_id: 'a1', type: 'message.user', payload: { text: '' } }]),
    ).toThrow();
    expect(store.list()).toHaveLength(0);
    expect(getProject(store.db, 'p1')).toBeUndefined();
  });

  it('notifies subscribers after commit, including ephemeral events', () => {
    const seen: StreamItem[] = [];
    const unsubscribe = store.subscribe((i) => seen.push(i));
    store.append(project());
    store.publishEphemeral({ type: 'assistant.delta', project_id: 'p1', agent_id: 'a1', payload: { run_id: 'r', text: 'x' } });
    unsubscribe();
    store.append(agent());
    expect(seen.map((i) => i.kind)).toEqual(['event', 'ephemeral']);
  });

  it('indexes events by project, type and id for the message fold', () => {
    const names = store.db.all<{ name: string }>(sql`SELECT name FROM sqlite_master WHERE type = 'index' AND tbl_name = 'events'`).map((r) => r.name);
    expect(names).toContain('events_project_type_idx');
  });
});
