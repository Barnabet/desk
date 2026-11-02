import { join } from 'node:path';
import { afterEach, describe, expect, it } from 'vitest';
import { call, text, tools, type ChatRequest } from '@desk/fake-model';
import { getDeskAgent } from '../state/queries';
import { createHarness, FAKE_MODEL, newRuntime, type Harness } from '../testing/harness';
import { STATE_HEADER } from './transcript';

let h: Harness;
afterEach(async () => h?.cleanup());

const content = (m: ChatRequest['messages'][number] | undefined) => (typeof m?.content === 'string' ? m.content : JSON.stringify(m?.content ?? null));

/**
 * Prompt caching reuses a request's longest unchanged prefix: tools, system prompt, earlier messages. A new turn must
 * only add to the end, whatever changed in the project meanwhile.
 */
describe('the cacheable prefix across turns', () => {
  it("keeps Desk's tools, system prompt and earlier messages byte-identical across a user message, the new state at the end", async () => {
    h = await createHarness({
      script: (req) => {
        const last = req.messages.at(-1)!;
        if (last.role === 'tool') return text('Planned.');
        if (content(last) === 'Kick off') return tools(call('update_plan', { items: [{ title: 'Research', status: 'todo' }] }));
        return text('Noted.');
      },
    });
    const rt = newRuntime(h);
    const projectId = rt.createProject({ name: 'Relaunch', goal: 'Relaunch the site', settings: { desk_model: FAKE_MODEL.id, thread_model: FAKE_MODEL.id } });
    const desk = getDeskAgent(h.store.db, projectId)!;
    rt.sendToDesk(projectId, 'Kick off');
    await rt.whenIdle();
    const before = h.fake.requests.at(-1)!;

    // Between turns the project changes: a thread appears, memory and the library grow.
    const thread = rt.createThread(projectId, { title: 'Audit', brief: 'Audit the pages.', workspacePath: join(h.dir, 'audit') });
    h.store.append({ project_id: projectId, agent_id: thread, type: 'agent.status_changed', payload: { status: 'cancelled' } });
    rt.writeMemory(projectId, { kind: 'decision', content: 'Launch on Friday' });
    rt.addLibraryFile(projectId, { name: 'audit.md', content: 'x', title: 'Audit notes' });
    rt.sendToDesk(projectId, 'Anything new?');
    await rt.whenIdle();
    const after = h.fake.requests.at(-1)!;

    expect(JSON.stringify(after.tools)).toBe(JSON.stringify(before.tools));
    expect(after.messages[0]).toEqual(before.messages[0]);
    // Every message of the earlier request, byte for byte, then the reply it got.
    const prefix = after.messages.slice(0, before.messages.length);
    expect(JSON.stringify(prefix)).toBe(JSON.stringify(before.messages));
    expect(after.messages.slice(before.messages.length).map((m) => [m.role, content(m).split('\n')[0]])).toEqual([
      ['assistant', 'Planned.'],
      ['user', expect.stringMatching(new RegExp(`^${STATE_HEADER.replace(/[[\]]/g, '\\$&')} at .*: only the sections that changed`))],
      ['user', 'Anything new?'],
    ]);
    // The new note holds only what changed since the last one: the plan Desk wrote during that turn, the thread, memory
    // and library; not What's up (still unwritten), nor the sections that are still empty.
    const note = content(after.messages.at(-2));
    expect(note).toContain('## Plan\n');
    expect(note).toContain('Research');
    expect(note).not.toContain("## What's up");
    expect(note).toContain('## Threads\n- ');
    expect(note).toContain('"Audit" [cancelled]');
    expect(note).toContain('Launch on Friday');
    expect(note).toContain('audit.md — Audit notes');
    expect(note).not.toContain('## Services');
    // The first turn's note was complete, and it is still in the prefix unchanged.
    expect(content(before.messages[1])).toMatch(/^\[Desk runtime — project state at [^\n]*UTC\]\n\n## Plan\n\(no plan yet\)/);
    expect(h.store.list({ agentId: desk.id, types: ['context.state'] })).toHaveLength(2);
  });

  it('adds no state note when nothing changed, so the new turn is only the new message', async () => {
    h = await createHarness({ script: () => text('Noted.') });
    const rt = newRuntime(h);
    const projectId = rt.createProject({ name: 'Quiet', goal: 'Nothing', settings: { desk_model: FAKE_MODEL.id, thread_model: FAKE_MODEL.id } });
    rt.sendToDesk(projectId, 'One');
    await rt.whenIdle();
    rt.sendToDesk(projectId, 'Two');
    await rt.whenIdle();
    const [first, second] = h.fake.requests;
    expect(JSON.stringify(second!.messages.slice(0, first!.messages.length))).toBe(JSON.stringify(first!.messages));
    expect(second!.messages.slice(first!.messages.length).map((m) => [m.role, content(m)])).toEqual([
      ['assistant', 'Noted.'],
      ['user', 'Two'],
    ]);
  });

  it("keeps a thread's prefix when a sibling joins the team, the Team section arriving as a note", async () => {
    h = await createHarness({ script: (req) => (content(req.messages.at(-1)) === 'Go' ? text('Started.') : text('Seen.')) });
    const rt = newRuntime(h);
    const projectId = rt.createProject({ name: 'Shop', goal: 'Ship', settings: { desk_model: FAKE_MODEL.id, thread_model: FAKE_MODEL.id } });
    const me = rt.createThread(projectId, { title: 'Me', brief: 'Do it.', workspacePath: join(h.dir, 'me') });
    rt.sendMessage(me, 'Go');
    await rt.whenIdle();
    const before = h.fake.requests.filter((r) => content(r.messages[0]).startsWith('You are a Desk thread')).at(-1)!;
    const sibling = rt.createThread(projectId, { title: 'Peer', brief: 'Do the other part.', workspacePath: join(h.dir, 'peer') });
    h.store.append({ project_id: projectId, agent_id: sibling, type: 'agent.status_changed', payload: { status: 'cancelled' } });
    rt.sendMessage(me, 'Again');
    await rt.whenIdle();
    const after = h.fake.requests.filter((r) => content(r.messages[0]).startsWith('You are a Desk thread')).at(-1)!;
    expect(JSON.stringify(after.messages.slice(0, before.messages.length))).toBe(JSON.stringify(before.messages));
    const added = after.messages.slice(before.messages.length).map(content);
    expect(added[1]).toContain(`## Team\nOther threads in this project`);
    expect(added[1]).toContain(`- ${sibling} "Peer" — brief: "Do the other part."`);
    expect(added.at(-1)).toBe('Again');
  });
});
