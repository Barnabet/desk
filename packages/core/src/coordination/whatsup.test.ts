import { join } from 'node:path';
import { afterEach, describe, expect, it } from 'vitest';
import { call, text, tools, type ChatRequest, type FakeReply } from '@desk/fake-model';
import { getDeskAgent } from '../state/queries';
import { createHarness, FAKE_MODEL, newRuntime, type Harness } from '../testing/harness';
import { latestWhatsUp, REMINDER_LABEL } from './whatsup';

let h: Harness;
afterEach(async () => h?.cleanup());

const content = (m: ChatRequest['messages'][number] | undefined) => (typeof m?.content === 'string' ? m.content : '');
/** Desk's replies, keyed on its latest input: a user brief, a tool result, a thread notice or the runtime's reminder. */
type DeskScript = (last: string, req: ChatRequest) => FakeReply;

async function setup(deskScript: DeskScript, threadReplies: FakeReply[] = []) {
  h = await createHarness({
    script: (req) => {
      if (req.model === FAKE_MODEL.id) return threadReplies.shift() ?? text('(thread idle)');
      const last = req.messages.at(-1);
      return deskScript(last?.role === 'tool' ? '(tool result)' : content(last), req);
    },
  });
  const rt = newRuntime(h, { whatsUpReminder: true });
  const projectId = rt.createProject({ name: 'P', goal: 'G', settings: { thread_model: FAKE_MODEL.id } });
  const desk = getDeskAgent(h.store.db, projectId)!;
  return { rt, projectId, desk };
}

const reminders = (deskId: string) => h.store.list({ agentId: deskId, types: ['message.agent'] }).filter((e) => e.type === 'message.agent' && e.payload.kind === 'reminder');
const deskRequests = () => h.fake.requests.filter((r) => r.model !== FAKE_MODEL.id);

describe("What's up", () => {
  it('update_whats_up replaces the text and the latest one is what the project shows', async () => {
    const { rt, projectId, desk } = await setup((last) =>
      last === 'Kick off' ? tools(call('update_whats_up', { text: 'Scoping the relaunch.' })) : last === 'More' ? tools(call('update_whats_up', { text: '  Two threads on it.  ' })) : text(''),
    );
    rt.sendToDesk(projectId, 'Kick off');
    await rt.whenIdle();
    rt.sendToDesk(projectId, 'More');
    await rt.whenIdle();
    expect(latestWhatsUp(h.store.db, desk.id)).toMatchObject({ text: 'Two threads on it.' });
    expect(h.store.list({ agentId: desk.id, types: ['whats_up.updated'] })).toHaveLength(2);
    expect(reminders(desk.id)).toHaveLength(0);
    // Desk sees its current What's up (and its age) in its instructions.
    const system = deskRequests().map((r) => content(r.messages[0]));
    expect(system[0]).toContain("## What's up\n(not written yet — write it with update_whats_up)");
    expect(system.at(-1)).toContain("## What's up\nScoping the relaunch.\n(written just now)");
  });

  it('reminds Desk once when it ends a turn after changing things without updating it, and Desk updates it', async () => {
    const { rt, projectId, desk } = await setup((last) => {
      if (last === 'Plan it') return tools(call('update_plan', { items: [{ title: 'Research', status: 'todo' }] }));
      if (last.startsWith(`[from ${REMINDER_LABEL} — reminder]`)) return tools(call('update_whats_up', { text: 'Planned the research.' }));
      return text(last === '(tool result)' ? 'Planned.' : '');
    });
    rt.sendToDesk(projectId, 'Plan it');
    await rt.whenIdle();
    expect(reminders(desk.id)).toHaveLength(1);
    expect(latestWhatsUp(h.store.db, desk.id)?.text).toBe('Planned the research.');
    // Brief, tool result, reminder, and the update's tool result: nothing after that.
    expect(deskRequests()).toHaveLength(4);
  });

  it('does not remind again when Desk ignores the reminder', async () => {
    const { rt, projectId, desk } = await setup((last) => (last === 'Plan it' ? tools(call('update_plan', { items: [{ title: 'Research', status: 'todo' }] })) : text('Noted.')));
    rt.sendToDesk(projectId, 'Plan it');
    await rt.whenIdle();
    expect(reminders(desk.id)).toHaveLength(1);
    expect(deskRequests()).toHaveLength(3);
  });

  it('reminds Desk after a thread reports and Desk only replies', async () => {
    const { rt, desk } = await setup((last) => (last.startsWith(`[from ${REMINDER_LABEL}`) ? tools(call('update_whats_up', { text: 'Research is in.' })) : text('Noted.')), [
      tools(call('complete', { summary: 'Found 3 facts', artifacts: [] })),
    ]);
    const threadId = rt.createThread(desk.project_id, { title: 'Research', brief: 'Find facts', workspacePath: join(h.dir, 'ws') });
    rt.sendMessage(threadId, 'go');
    await rt.whenIdle();
    expect(reminders(desk.id)).toHaveLength(1);
    expect(latestWhatsUp(h.store.db, desk.id)?.text).toBe('Research is in.');
  });

  it('does not remind Desk after a turn that only read or answered', async () => {
    const { rt, projectId, desk } = await setup((last) => (last === 'Status?' ? tools(call('list_threads', {})) : text('Nothing running yet.')));
    rt.sendToDesk(projectId, 'Status?');
    await rt.whenIdle();
    expect(reminders(desk.id)).toHaveLength(0);
  });

  it('does not remind Desk when it updated What’s up after its last change, even in the same step', async () => {
    const { rt, projectId, desk } = await setup((last) =>
      last === 'Plan it'
        ? tools(call('update_plan', { items: [{ title: 'Research', status: 'todo' }] }), call('update_whats_up', { text: 'Planned the research.' }))
        : text('Planned.'),
    );
    rt.sendToDesk(projectId, 'Plan it');
    await rt.whenIdle();
    expect(reminders(desk.id)).toHaveLength(0);
  });

  it('is off unless the runtime turns it on (tests default to off)', async () => {
    h = await createHarness({ script: (req) => (req.messages.at(-1)?.role === 'tool' ? text('Planned.') : tools(call('update_plan', { items: [{ title: 'R', status: 'todo' }] }))) });
    const rt = newRuntime(h);
    const projectId = rt.createProject({ name: 'P', goal: 'G' });
    rt.sendToDesk(projectId, 'Plan it');
    await rt.whenIdle();
    expect(reminders(getDeskAgent(h.store.db, projectId)!.id)).toHaveLength(0);
  });
});
