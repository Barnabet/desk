import { symlinkSync, writeFileSync } from 'node:fs';
import { join } from 'node:path';
import { afterEach, describe, expect, it } from 'vitest';
import { text } from '@desk/fake-model';
import { RUN_REPORT_MAX } from '@desk/protocol';
import { listArtifacts } from '../library/library';
import type { AgentRow } from '../state/queries';
import { automationHarness } from '../testing/automations';
import type { Harness } from '../testing/harness';
import type { Runtime } from '../runtime/runtime';
import { logFile, stepDir } from './folders';
import { getRun } from './queries';
import { formatDuration, runReport, tailOf } from './report';
import { localStamp, systemTimezone } from './schedule';

let h: Harness;
let rt: Runtime;
let projectId: string;
let desk: AgentRow;
afterEach(async () => h?.cleanup());

/** `x.py` writes digest.md (and a symlink) and succeeds; `boom.py` fails with a log; `huge.py` returns big outputs. */
async function setup() {
  ({ h, rt, projectId, desk } = await automationHarness({ script: () => text('ok') }));
  rt.saveSkill(
    { scope: 'project', projectId, name: 'x', description: 'test skill', instructions: 'Run', files: ['x.py', 'boom.py', 'huge.py'].map((f) => ({ path: `scripts/${f}`, content: 'print(1)' })) },
    { projectId },
  );
  rt.engine.register('script', ({ run, step }) => {
    if (step.kind !== 'script') return;
    const dir = stepDir(h.dir, run.id, step.id);
    if (step.script === 'boom.py') {
      writeFileSync(logFile(h.dir, run.id, step.id), `${'noise\n'.repeat(2000)}Traceback: the real cause\n`);
      return rt.engine.resolveStep(run.id, step.id, { status: 'failed', error: 'Exit code 1: Traceback: the real cause' });
    }
    if (step.script === 'huge.py') {
      const outputs = Object.fromEntries(Array.from({ length: 4 }, (_, i) => [`k${i}`, 'x'.repeat(3900)]));
      return rt.engine.resolveStep(run.id, step.id, { status: 'succeeded', route: null, outputs, summary: 'y'.repeat(2000) });
    }
    writeFileSync(join(dir, 'digest.md'), '# Digest');
    writeFileSync(join(h.files, 'secret.md'), 'not for the library');
    symlinkSync(join(h.files, 'secret.md'), join(dir, 'linked.md'));
    rt.engine.resolveStep(run.id, step.id, { status: 'succeeded', route: 'changed', outputs: { count: 3 }, summary: 'Fetched 3 pages' });
  });
}
const create = (name: string, def: object) => rt.automations.create(projectId, name, def, { origin: 'user', via: 'editor' }).automation.id;
const start = (id: string, o: { test?: boolean; by?: string } = {}) =>
  rt.engine.startRun(id, { trigger: o.test ? 'test' : o.by ? 'desk' : 'manual', test: o.test ?? false, inputs: {}, by: o.by ?? 'user' });
const deskTexts = () => h.store.list({ agentId: desk.id, types: ['message.agent'] }).map((e) => (e.type === 'message.agent' ? e.payload.text : ''));
const digest = (extra: object = {}) => ({
  title: 'Digest',
  steps: [
    { id: 'fetch', title: 'Fetch pages', kind: 'script', skill: 'x', script: 'x.py', routes: ['changed'], publish: ['*.md'] },
    { id: 'ok', title: 'Go ahead?', kind: 'ask', question: 'Publish {{steps.fetch.outputs.count}} pages?' },
  ],
  edges: [{ from: 'fetch', to: 'ok', route: 'changed' }],
  ...extra,
});

describe('run reports and after-run', () => {
  it('formats durations and log tails', () => {
    expect([850, 42_000, 185_000, 7_800_000].map(formatDuration)).toEqual(['850 ms', '42 s', '3 min 5 s', '2 h 10 min']);
    expect(tailOf('/nope/missing.txt', 100)).toBe('');
  });

  it('publishes matching files under the run stamp (never through a symlink)', async () => {
    await setup();
    const id = create('digest', digest());
    const r = await start(id);
    const t = await start(id, { test: true });
    await rt.engine.settled();
    const stamp = (runId: string) => localStamp(systemTimezone(), new Date(getRun(h.store.db, runId)!.started_at));
    const items = listArtifacts(h.store.db, projectId);
    expect(items.map((a) => [a.path, a.origin])).toEqual([
      [`automations/digest/${stamp(r)}/digest.md`, `automation:${r}`],
      [`automations/digest/tests/${stamp(t)}/digest.md`, `automation:${t}`],
    ]);
    expect(items[0]).toMatchObject({ title: 'digest.md', kind: 'file', description: `From "Fetch pages" in automation "Digest" (run ${r})` });
  });

  it('reports every step in order, with what a waiting step waits on and the published paths', async () => {
    await setup();
    const r = await start(create('digest', digest()));
    await rt.engine.settled();
    const report = runReport(h.store.db, h.dir, r);
    expect(report).toMatch(/^Automation "Digest" \(digest\), v1 · run \S+\nTrigger: manual by user · Status: waiting · Took /);
    const fetch = report.indexOf('1. Fetch pages [script] — succeeded · route changed');
    const ok = report.indexOf('2. Go ahead? [ask] — waiting');
    expect(fetch).toBeGreaterThan(0);
    expect(ok).toBeGreaterThan(fetch);
    expect(report).toContain('   Summary: Fetched 3 pages\n   Outputs: {"count":3}');
    expect(report).toContain(`   Folder: ${stepDir(h.dir, r, 'fetch')}`);
    expect(report).toContain('   Waiting on: Ask me: Publish 3 pages?');
    expect(report).toMatch(/Published to the library:\n- automations\/digest\/.+\/digest\.md/);
  });

  it('shows the log tail of a failed script and stays within the cap', async () => {
    await setup();
    const r = await start(
      create('mixed', {
        title: 'Mixed',
        steps: [
          { id: 'big', title: 'Big', kind: 'script', skill: 'x', script: 'huge.py' },
          { id: 'bad', title: 'Bad', kind: 'script', skill: 'x', script: 'boom.py' },
        ],
        edges: [{ from: 'big', to: 'bad' }],
      }),
    );
    const report = runReport(h.store.db, h.dir, r);
    expect(report.length).toBeLessThanOrEqual(RUN_REPORT_MAX);
    expect(report).toContain('Status: failed');
    expect(report).toContain('Reason: bad failed: Exit code 1: Traceback: the real cause');
    expect(report).toContain(`   Log: ${logFile(h.dir, r, 'bad')}`);
    expect(report).toMatch(/Log tail:\n[\s\S]*Traceback: the real cause/); // the tail keeps its end
    expect(report).toContain('…[trimmed]');
  });

  it('delivers the report to Desk for desk_review and for runs Desk started, never for sub-automation runs', async () => {
    await setup();
    const plain = { title: 'Plain', steps: [{ id: 'fetch', title: 'Fetch pages', kind: 'script', skill: 'x', script: 'x.py', routes: ['changed'], publish: ['*.md'] }] };
    create('plain', plain);
    await start(rt.automations.requireByName(projectId, 'plain').id); // notify: nothing for Desk
    const review = await start(create('review', { ...plain, title: 'Review', after_run: 'desk_review' }));
    await rt.engine.settled();
    expect(deskTexts()).toHaveLength(1);
    expect(deskTexts()[0]).toMatch(new RegExp(`^Run ${review} succeeded\\. This automation asks you to review each run\\.\\n\\nAutomation "Review"`));
    expect(deskTexts()[0]).toContain('Published to the library:'); // publishing finished before the report

    const parent = create('parent', { title: 'Parent', steps: [{ id: 'sub', title: 'Sub', kind: 'automation', automation: 'review' }], after_run: 'silent' });
    const byDesk = await start(parent, { by: `agent:${desk.id}` });
    await rt.engine.settled();
    expect(deskTexts()).toHaveLength(2); // the parent's report only: the child (desk_review) is a sub-automation run
    expect(deskTexts()[1]).toMatch(new RegExp(`^Run ${byDesk} succeeded\\.\\n\\nAutomation "Parent"`));
  });

  it('tells Desk when a run it started waits on the user', async () => {
    await setup();
    const r = await start(create('digest', digest()), { by: `agent:${desk.id}` });
    await rt.engine.settled();
    expect(deskTexts()).toEqual([`Run ${r} is waiting for the user at "Go ahead?" (Ask me: Publish 3 pages?). Only the user can answer, in the app. You'll get the report when the run finishes.`]);
    await rt.engine.answer(r, 'ok', { decision: 'approve' });
    await rt.engine.settled();
    expect(deskTexts()[1]).toMatch(new RegExp(`^Run ${r} succeeded\\.`));
  });
});
