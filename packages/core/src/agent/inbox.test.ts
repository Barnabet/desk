import { afterEach, beforeEach, describe, expect, it } from 'vitest';
import { openDb } from '../db/open';
import { EventStore } from '../events/store';
import { getAgent } from '../state/queries';
import { drainInbox, hasPendingInbox, pendingInbox } from './inbox';

let close: () => void;
let store: EventStore;
beforeEach(() => {
  const o = openDb(':memory:');
  close = o.close;
  store = new EventStore(o.db);
  store.append([
    { project_id: 'p', agent_id: null, type: 'project.created', payload: { name: 'P', goal: '', instructions: '' } },
    { project_id: 'p', agent_id: 'a', type: 'agent.created', payload: { role: 'thread', model: 'm', title: null, brief: null, workspace_path: '/w', parent_id: null } },
  ]);
});
afterEach(() => close());

describe('inbox', () => {
  it('drains pending messages up to the latest id', () => {
    expect(hasPendingInbox(store, 'a')).toBe(false);
    const [m1] = store.append({ project_id: 'p', agent_id: 'a', type: 'message.user', payload: { text: 'one' } });
    const [m2] = store.append({ project_id: 'p', agent_id: 'a', type: 'message.user', payload: { text: 'two' } });
    expect(hasPendingInbox(store, 'a')).toBe(true);
    expect(drainInbox(store, 'a', 'r1')).toBe(2);
    expect(getAgent(store.db, 'a')?.inbox_cursor).toBe(m2!.id);
    expect(m1!.id).toBeLessThan(m2!.id);
    expect(hasPendingInbox(store, 'a')).toBe(false);
    expect(drainInbox(store, 'a', 'r1')).toBe(0);
    expect(store.list({ types: ['inbox.drained'] })).toHaveLength(1);
  });

  it('lists the events stored after the cursor', () => {
    const [m1] = store.append({ project_id: 'p', agent_id: 'a', type: 'message.user', payload: { text: 'one' } });
    drainInbox(store, 'a', 'r1');
    const [m2] = store.append({ project_id: 'p', agent_id: 'a', type: 'message.agent', payload: { from_agent_id: 'd', from_label: 'Desk', kind: 'note', text: 'two' } });
    expect(pendingInbox(store, 'a').map((e) => e.id)).toEqual([m2!.id]);
    expect(m1!.id).toBeLessThan(m2!.id);
  });
});
