import { afterEach, describe, expect, it } from 'vitest';
import { call, text, tools, type ChatRequest, type FakeReply } from '@desk/fake-model';
import { listAttention } from '../state/attention';
import { getDeskAgent } from '../state/queries';
import { automationHarness } from '../testing/automations';
import type { Harness } from '../testing/harness';
import { findAutomation, getStepRun, listRuns, testedVersion } from './queries';

let h: Harness;
afterEach(async () => h?.cleanup());

/** The script Desk installs: counts the topic's words and returns them as an output. */
const COUNT = `#!/bin/bash
words=$(echo "$1" | wc -w | tr -d ' ')
printf '{"summary": "Counted %s word(s)", "outputs": {"words": %s}}' "$words" "$words" > "$DESK_OUTPUT"
`;

const DEFINITION = {
  title: 'Weekly digest',
  description: 'Summarises the week in a topic and asks before publishing.',
  inputs: [{ key: 'topic', label: 'Topic', type: 'text', default: 'AI' }],
  triggers: [{ kind: 'schedule', cron: '0 8 * * 1', timezone: 'Europe/Paris', catch_up: 'once' }],
  steps: [
    { id: 'count', title: 'Count words', kind: 'script', skill: 'counter', script: 'count.sh', args: ['{{inputs.topic}}'] },
    { id: 'sum', title: 'Summarise', kind: 'agent', brief: 'Summarise this week in {{inputs.topic}} ({{steps.count.outputs.words}} word topic).', output_keys: [{ key: 'headline', description: 'the week in one line' }] },
    { id: 'ok', title: 'Publish?', kind: 'ask', question: 'Publish "{{steps.sum.outputs.headline}}"?' },
  ],
  edges: [
    { from: 'count', to: 'sum' },
    { from: 'sum', to: 'ok' },
  ],
};

const content = (m: ChatRequest['messages'][number] | undefined) => String(m?.content ?? '');

/** Desk: write the script skill → save → test → wait; tell the user about waits; on the report, propose turning it on. */
function desk(req: ChatRequest): FakeReply {
  const last = req.messages.at(-1);
  const said = content(last);
  if (last?.role === 'tool') {
    if (said.startsWith('Created project skill "counter"')) return tools(call('automation_save', { name: 'digest', definition: DEFINITION, change_note: 'First version' }));
    if (said.startsWith('Saved digest v1.')) return tools(call('automation_test', { name: 'digest', inputs: { topic: 'robots' } }));
    const started = /Started test run (\S+)/.exec(said);
    if (started) return tools(call('wait_for_run', { run_id: started[1] }));
    if (said.startsWith('Asked the user to turn on digest')) return text('Digest is tested; turn it on when you are ready.');
    return text('ok');
  }
  if (said.includes('Every Monday')) {
    return tools(call('skill_write', { name: 'counter', scope: 'project', description: 'Counts the words of a text.', instructions: 'Run scripts/count.sh <text>.', files: [{ path: 'scripts/count.sh', content: COUNT }], change_note: 'For the digest automation' }));
  }
  if (/Run \S+ succeeded\./.test(said)) return tools(call('automation_request_enable', { name: 'digest', note: 'Tested with robots: the headline read well.' }));
  if (said.includes('is waiting for the user')) return text('The test run is waiting for your answer in the app.');
  return text('ok');
}

describe('automations end to end', () => {
  it('Desk writes a script skill, builds, tests and proposes an automation; the user answers; only the user turns it on', async () => {
    const r = await automationHarness({
      script: (req) => (content(req.messages[0]).includes('You are one step of the automation') ? tools(call('complete', { summary: 'Robots had a big week', outputs: { headline: 'Robots learn to fold laundry' } })) : desk(req)),
    });
    h = r.h;
    const { rt, projectId } = r;
    const deskAgent = getDeskAgent(h.store.db, projectId)!;
    const settle = async () => {
      await rt.whenIdle();
      await rt.engine.settled();
      await rt.whenIdle();
    };

    rt.sendToDesk(projectId, 'Every Monday at 8, summarise the week in robots and ask me before publishing.');
    await settle();

    const a = findAutomation(h.store.db, projectId, 'digest')!;
    expect(a).toMatchObject({ version: 1, enabled: false });
    const [run] = listRuns(h.store.db, a.id);
    expect(run).toMatchObject({ trigger: 'test', test: true, by: `agent:${deskAgent.id}`, inputs: { topic: 'robots' }, status: 'running' });
    // No sandbox in the harness: the script waits on its gate, which the user approves and remembers.
    expect(getStepRun(h.store.db, run!.id, 'count')).toMatchObject({ status: 'waiting', gate: { tool: 'skill_run', subject: 'counter/count.sh robots' } });
    await rt.engine.answer(run!.id, 'count', { decision: 'approve', remember: true });
    expect(h.store.list({ projectId, types: ['automation.grants_set'] }).at(-1)!.payload).toMatchObject({ reason: 'remembered', source: { run_id: run!.id, step_id: 'count' } });
    await settle();

    expect(getStepRun(h.store.db, run!.id, 'count')).toMatchObject({ status: 'succeeded', outputs: { words: 1 } });
    expect(findAutomation(h.store.db, projectId, 'digest')!.grants).toEqual([{ tool: 'skill_run', match: { command: '^counter/count\\.sh(\\s|$)' }, action: 'allow' }]);
    expect(listAttention(h.store.db, { projectId }).find((i) => i.kind === 'automation_ask')).toMatchObject({ title: 'Weekly digest: Publish "Robots learn to fold laundry"?' });
    await rt.engine.answer(run!.id, 'ok', { decision: 'approve', note: 'Nice' });
    await settle();

    expect(listRuns(h.store.db, a.id)[0]!.status).toBe('succeeded');
    expect(testedVersion(h.store.db, a.id)).toBe(1);
    const after = findAutomation(h.store.db, projectId, 'digest')!;
    expect(after.enabled).toBe(false);
    expect(after.enable_request).toMatchObject({ note: 'Tested with robots: the headline read well.' });
    expect(listAttention(h.store.db, { projectId }).map((i) => i.kind)).toContain('automation_enable_request');
    const texts = h.store.list({ agentId: deskAgent.id, types: ['assistant.message'] }).map((e) => (e.type === 'assistant.message' ? e.payload.content : ''));
    expect(texts.filter((t) => t === 'The test run is waiting for your answer in the app.')).toHaveLength(2); // the gate, then the question
    expect(texts.at(-1)).toBe('Digest is tested; turn it on when you are ready.');
  });
});
