import { existsSync, readFileSync, writeFileSync } from 'node:fs';
import { homedir } from 'node:os';
import { join } from 'node:path';
import { afterEach, beforeAll, describe, expect, it } from 'vitest';
import { DEFAULT_POLICY } from '@desk/protocol';
import { detectSandbox, NO_SANDBOX } from '../tools/sandbox';
import { automationHarness, type FakeClock } from '../testing/automations';
import type { Harness } from '../testing/harness';
import type { Runtime } from '../runtime/runtime';
import { getAutomation, getRun, getStepRun } from './queries';
import { logFile, stepDir } from './folders';
import { lastLine, parseStepOutput, runScript } from './script';

let h: Harness;
let rt: Runtime;
let projectId: string;
let clock: FakeClock;
let sandbox = false;
beforeAll(async () => {
  sandbox = await detectSandbox();
});
afterEach(async () => h?.cleanup());

/** A project skill `mine` with shell scripts. */
const EMIT = `#!/bin/bash
printf '%s' "$1" > "$DESK_STEP_DIR/arg1.txt"
echo "test=\${DESK_TEST:-0} inputs=$(cat "$DESK_INPUTS")"
cat > "$DESK_OUTPUT" <<JSON
{"route": "changed", "summary": "Fetched $2 pages", "outputs": {"count": $2, "first": "$1"}}
JSON
`;
const FAIL = `#!/bin/bash\necho "about to fail"\necho "bad input" >&2\nexit 3\n`;
const PLAIN = `#!/bin/bash\necho "line one"\necho "all done"\n`;
const BADROUTE = `#!/bin/bash\necho '{"route": "nope"}' > "$DESK_OUTPUT"\n`;
const SLEEPY = `#!/bin/bash\nsleep 30\n`;
/** Plants symlinks where a script might hope deskd reads or writes: DESK_OUTPUT, and the log's old spot `.desk/log.txt`. */
const PLANT = `#!/bin/bash\nln -sf "$1" "$DESK_OUTPUT"\nln -sf "$1" "$DESK_STEP_DIR/.desk/log.txt"\necho planted\n`;
const ESCAPE = `#!/bin/bash\necho x > "$HOME/desk-script-escape-test" 2>/dev/null && echo escaped\necho ok > "$DESK_STEP_DIR/inside.txt"\ncat "$1"\n`;

async function setup(extra: Parameters<typeof automationHarness>[0] = {}) {
  ({ h, rt, projectId, clock } = await automationHarness(extra));
  rt.saveSkill(
    {
      scope: 'project',
      projectId,
      name: 'mine',
      description: 'Test scripts',
      instructions: 'Scripts for tests.',
      files: Object.entries({ 'emit.sh': EMIT, 'fail.sh': FAIL, 'plain.sh': PLAIN, 'badroute.sh': BADROUTE, 'sleepy.sh': SLEEPY, 'escape.sh': ESCAPE, 'plant.sh': PLANT }).map(([f, content]) => ({ path: `scripts/${f}`, content })),
    },
    { projectId },
  );
  // The harness has no sandbox, where every unmatched skill_run asks: the user's rule lets the test skill run.
  rt.updateSettings(projectId, { policy: [{ tool: 'skill_run', match: { command: '^mine/' }, action: 'allow' }, ...DEFAULT_POLICY] });
}
const script = (id: string, file: string, extra: object = {}) => ({ id, title: id, kind: 'script', skill: 'mine', script: file, ...extra });
const create = (def: object, name = 'a') => rt.automations.create(projectId, name, def, { origin: 'user', via: 'editor' }).automation.id;
const start = (id: string, opts: { test?: boolean; inputs?: object } = {}) =>
  rt.engine.startRun(id, { trigger: opts.test ? 'test' : 'manual', test: opts.test ?? false, inputs: (opts.inputs ?? {}) as never, by: 'user' });
/** Resolves once the run has ended. */
async function settled(runId: string, ms = 10_000): Promise<void> {
  const until = Date.now() + ms;
  while (getRun(h.store.db, runId)?.status === 'running') {
    if (Date.now() > until) throw new Error(`run ${runId} still running`);
    await new Promise((r) => setTimeout(r, 20));
  }
}
/** Resolves once the step reaches `status`. */
async function stepIs(runId: string, stepId: string, status: string, ms = 10_000): Promise<void> {
  const until = Date.now() + ms;
  while (getStepRun(h.store.db, runId, stepId)?.status !== status) {
    if (Date.now() > until) throw new Error(`${stepId} is ${getStepRun(h.store.db, runId, stepId)?.status}, not ${status}`);
    await new Promise((r) => setTimeout(r, 20));
  }
}

describe('script output', () => {
  it('parses DESK_OUTPUT and checks routes', () => {
    expect(parseStepOutput('{"route":"a","summary":"s","outputs":{"n":1}}', ['a'])).toEqual({ ok: true, route: 'a', outputs: { n: 1 }, summary: 's' });
    expect(parseStepOutput('{}', [])).toEqual({ ok: true, route: null, outputs: {}, summary: null });
    expect(parseStepOutput('not json', [])).toMatchObject({ ok: false, error: expect.stringMatching(/not valid JSON/) });
    expect(parseStepOutput('{"route":"b"}', ['a'])).toMatchObject({ ok: false, error: expect.stringMatching(/"b" is not one of this step's routes \(a\)/) });
    expect(parseStepOutput('{"outputs":{"Bad":1}}', [])).toMatchObject({ ok: false });
    expect(lastLine('one\n\ntwo\n  \n')).toBe('two');
    expect(lastLine('')).toBeNull();
  });

  it('times out and aborts a process', async () => {
    ({ h } = await automationHarness());
    const file = join(h.files, 'slow.sh');
    writeFileSync(file, '#!/bin/bash\nsleep 5\n');
    const r = await runScript({ file, args: [], stepDir: h.files, env: process.env, sandbox: NO_SANDBOX, timeoutMs: 200, signal: new AbortController().signal, logFile: join(h.files, 'log.txt') });
    expect(r.timedOut).toBe(true);
    expect(existsSync(join(h.files, 'log.txt'))).toBe(true);
  });
});

describe('script steps', () => {
  it('runs a script with rendered argv, reads its output and writes its log', async () => {
    await setup();
    const id = create({
      title: 'Fetch',
      inputs: [{ key: 'site', label: 'Site', type: 'text' }],
      steps: [script('fetch', 'emit.sh', { args: ['{{inputs.site}}', '3'], routes: ['changed', 'unchanged'] })],
    });
    const hostile = '; rm -rf ~ && echo $(whoami) {{inputs.site}}';
    const r = await start(id, { test: true, inputs: { site: hostile } });
    await settled(r);
    expect(getStepRun(h.store.db, r, 'fetch')).toMatchObject({ status: 'succeeded', route: 'changed', summary: 'Fetched 3 pages', outputs: { count: 3, first: hostile } });
    expect(readFileSync(join(stepDir(h.dir, r, 'fetch'), 'arg1.txt'), 'utf8')).toBe(hostile);
    const log = readFileSync(logFile(h.dir, r, 'fetch'), 'utf8');
    expect(log).toContain('test=1');
    expect(log).toContain('"site"');
  });

  it('fails with the output tail, continues on error, and uses the last line without DESK_OUTPUT', async () => {
    await setup();
    const id = create({
      title: 'Mixed',
      steps: [script('bad', 'fail.sh', { on_error: 'continue' }), script('plain', 'plain.sh'), { id: 'after', title: 'After', kind: 'wait', minutes: 1 }],
      edges: [{ from: 'bad', to: 'after', route: 'error' }],
    });
    const r = await start(id);
    await stepIs(r, 'plain', 'succeeded');
    await stepIs(r, 'bad', 'failed');
    expect(getStepRun(h.store.db, r, 'bad')).toMatchObject({ route: 'error', error: expect.stringMatching(/^Exit code 3:[\s\S]*bad input/) });
    expect(getStepRun(h.store.db, r, 'plain')).toMatchObject({ summary: 'all done', outputs: {} });
    expect(getStepRun(h.store.db, r, 'after')!.status).toBe('waiting');
  });

  it('never follows a symlink the script planted for DESK_OUTPUT or the log', async () => {
    await setup();
    const outside = join(h.files, 'outside.json');
    writeFileSync(outside, '{"summary": "from outside"}');
    const r = await start(create({ title: 'Plant', steps: [script('p', 'plant.sh', { args: [outside] })] }));
    await settled(r);
    expect(getStepRun(h.store.db, r, 'p')!.error).toMatch(/^DESK_OUTPUT could not be read/);
    expect(readFileSync(outside, 'utf8')).toBe('{"summary": "from outside"}'); // the log went to <run>/logs, not through the link
    expect(readFileSync(logFile(h.dir, r, 'p'), 'utf8')).toContain('planted');
  });

  it('fails on an undeclared route', async () => {
    await setup();
    const r = await start(create({ title: 'Bad route', steps: [script('b', 'badroute.sh', { routes: ['ok'] })] }));
    await settled(r);
    expect(getStepRun(h.store.db, r, 'b')!.error).toMatch(/"nope" is not one of this step's routes/);
    expect(getRun(h.store.db, r)!.status).toBe('failed');
  });

  it('asks through the policy, remembers the grant, and does not ask again', async () => {
    await setup();
    rt.updateSettings(projectId, { policy: [{ tool: 'skill_run', match: { command: '^mine/emit\\.sh' }, action: 'ask' }, ...DEFAULT_POLICY] });
    const id = create({ title: 'Gated', steps: [script('fetch', 'emit.sh', { args: ['x', '1'], routes: ['changed'] })] });
    const r1 = await start(id);
    await stepIs(r1, 'fetch', 'waiting');
    expect(getStepRun(h.store.db, r1, 'fetch')).toMatchObject({ gate: { tool: 'skill_run', subject: 'mine/emit.sh x 1' } });
    await rt.engine.answer(r1, 'fetch', { decision: 'approve', remember: true });
    await settled(r1);
    expect(getStepRun(h.store.db, r1, 'fetch')!.status).toBe('succeeded');
    expect(getAutomation(h.store.db, id)!.grants).toEqual([{ tool: 'skill_run', match: { command: '^mine/emit\\.sh(\\s|$)' }, action: 'allow' }]);
    const r2 = await start(id);
    await settled(r2);
    expect(getStepRun(h.store.db, r2, 'fetch')!.gate).toBeNull();
    // Suspended grants behave as none.
    rt.automations.save(id, { title: 'Gated', steps: [script('fetch', 'emit.sh', { args: ['x', '2'], routes: ['changed'] })] }, { origin: 'agent:d', via: 'tool' });
    const r3 = await start(id);
    await stepIs(r3, 'fetch', 'waiting');
    await expect(rt.engine.answer(r3, 'fetch', { decision: 'approve', remember: true })).rejects.toThrow(/suspended/);
    await rt.engine.answer(r3, 'fetch', { decision: 'reject', note: 'not now' });
    expect(getStepRun(h.store.db, r3, 'fetch')).toMatchObject({ status: 'failed', error: 'Denied by the user: not now', answered_by: 'user' });
  });

  it('fails a step the policy denies', async () => {
    await setup();
    rt.updateSettings(projectId, { policy: [{ tool: 'skill_run', action: 'deny' }] });
    const r = await start(create({ title: 'Denied', steps: [script('p', 'plain.sh')] }));
    await settled(r);
    expect(getStepRun(h.store.db, r, 'p')!.error).toMatch(/^Denied by policy/);
  });

  it('kills a running script when the run is cancelled', async () => {
    await setup();
    const r = await start(create({ title: 'Sleepy', steps: [script('s', 'sleepy.sh')] }));
    await new Promise((res) => setTimeout(res, 300));
    const t0 = Date.now();
    await rt.engine.cancelRun(r, 'enough');
    expect(getStepRun(h.store.db, r, 's')!.status).toBe('cancelled');
    expect(Date.now() - t0).toBeLessThan(2000);
  });

  it('confines a script to its step folder under the sandbox', async () => {
    if (!sandbox) return;
    await setup({ extra: { sandboxAvailable: true } });
    const escapee = join(homedir(), 'desk-script-escape-test');
    const id = create({
      title: 'Escape',
      steps: [
        { id: 'w', title: 'Wait', kind: 'wait', minutes: 1 },
        script('e', 'escape.sh', { args: ['{{steps.w.dir}}/note.txt'] }),
      ],
      edges: [{ from: 'w', to: 'e' }],
    });
    const r = await start(id);
    writeFileSync(join(stepDir(h.dir, r, 'w'), 'note.txt'), 'upstream note');
    clock.advance(60_000);
    await rt.engine.tick();
    await settled(r);
    expect(existsSync(escapee)).toBe(false);
    const log = readFileSync(logFile(h.dir, r, 'e'), 'utf8');
    expect(log).not.toContain('escaped');
    expect(log).toContain('upstream note');
    expect(existsSync(join(stepDir(h.dir, r, 'e'), 'inside.txt'))).toBe(true);
  });
});
