import { afterEach, describe, expect, it } from 'vitest';
import { text } from '@desk/fake-model';
import { getProject } from '../state/queries';
import { automationHarness, waitDef } from '../testing/automations';
import type { Harness } from '../testing/harness';
import type { Runtime } from '../runtime/runtime';
import type { AgentRow } from '../state/queries';
import { AUTOMATIONS_IN_PROMPT, DESK_AUTOMATIONS_RULE, deskSystemPrompt } from './prompts';

let h: Harness;
let rt: Runtime;
let projectId: string;
let desk: AgentRow;
afterEach(async () => h?.cleanup());

const prompt = () => deskSystemPrompt({ db: h.store.db, agent: desk, project: getProject(h.store.db, projectId)!, libraryDir: rt.libraryDir(projectId) });

describe("Desk's prompt: automations", () => {
  it('has rule 13 after Trust, and rule 7 sends recurring processes there', async () => {
    ({ h, rt, projectId, desk } = await automationHarness({ script: () => text('ok') }));
    const p = prompt();
    expect(p).toContain(`\n${DESK_AUTOMATIONS_RULE}\n`);
    expect(p.indexOf('12. Trust')).toBeLessThan(p.indexOf('13. Automations'));
    expect(DESK_AUTOMATIONS_RULE.split('\n')).toHaveLength(8);
    expect(p).toContain('A process that should run by itself, on a schedule or on demand, is an automation (rule 13)');
    expect(p).not.toContain('when the user asks for an automation or a repeatable task');
    expect(p).toContain('Messages labelled automation "…" come from automation runs');
    expect(p).toContain('## Automations\n(none)');
  });

  it('lists automations with their switch, schedules, last run and flags, at most 20 lines', async () => {
    ({ h, rt, projectId, desk } = await automationHarness({ script: () => text('ok') }));
    const daily = { kind: 'schedule', cron: '0 8 * * *', timezone: 'Europe/Paris', catch_up: 'once' };
    const digest = rt.automations.create(projectId, 'digest', waitDef({ title: 'Morning digest', triggers: [daily] }), { origin: 'user', via: 'editor' }).automation.id;
    rt.automations.setEnabled(digest, true, 'user');
    const r = await rt.engine.startRun(digest, { trigger: 'test', test: true, inputs: {}, by: 'user' });
    rt.automations.create(projectId, 'inbox', { title: 'Inbox triage', steps: [{ id: 'ok', title: 'OK?', kind: 'ask', question: 'Go?' }] }, { origin: 'user', via: 'editor' });
    const started = h.store.list({ projectId, types: ['automation.run_started'] }).at(-1)!.ts;
    const lines = prompt().split('## Automations\n')[1]!.split('\n\n')[0]!.split('\n');
    expect(lines).toEqual([
      `- digest "Morning digest": on · 0 8 * * * (Europe/Paris) · last run running (test) ${started} · not tested`,
      '- inbox "Inbox triage": off · Run now only · never run · not tested',
    ]);
    void r;

    for (let i = 0; i < 20; i++) rt.automations.create(projectId, `extra-${String(i).padStart(2, '0')}`, waitDef(), { origin: 'user', via: 'editor' });
    const long = prompt().split('## Automations\n')[1]!.split('\n\n')[0]!.split('\n');
    expect(long).toHaveLength(AUTOMATIONS_IN_PROMPT + 1);
    expect(long.at(-1)).toBe(`… and ${22 - AUTOMATIONS_IN_PROMPT} more (automation_list)`);
  });
});
