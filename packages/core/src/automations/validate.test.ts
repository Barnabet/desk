import { describe, expect, it } from 'vitest';
import { AutomationDefinition, type ReasoningEffort } from '@desk/protocol';
import { issuePath, subAutomationCycle, validateDefinition, type ValidateContext } from './validate';

const NOW = new Date('2026-09-26T10:00:00.000Z');
const def = (raw: unknown): AutomationDefinition => AutomationDefinition.parse(raw);
const wait = (id: string) => ({ id, title: id, kind: 'wait', minutes: 1 });

/** The automation `digest` calls: it needs `folder`. */
const archive = def({
  title: 'Archive',
  inputs: [
    { key: 'folder', label: 'Folder', type: 'folder', required: true },
    { key: 'label', label: 'Label', type: 'text' },
  ],
  steps: [wait('w')],
});

/** A valid definition with every step kind; tests change one thing each. */
const base = (): any => ({
  title: 'Weekly digest',
  inputs: [
    { key: 'sources', label: 'Sources', type: 'file', required: true },
    { key: 'tone', label: 'Tone', type: 'choice', options: ['short', 'long'], default: 'short' },
  ],
  triggers: [{ kind: 'schedule', cron: '0 8 * * 1', timezone: 'Europe/Paris', inputs: { sources: '/Users/me/sources.txt' } }],
  steps: [
    {
      id: 'fetch',
      title: 'Fetch',
      kind: 'script',
      skill: 'mine',
      script: 'fetch.py',
      args: ['--urls', '{{inputs.sources}}', '--since', '{{previous.steps.fetch.outputs.last}}'],
      routes: ['changed', 'nothing_new'],
    },
    {
      id: 'summarise',
      title: 'Summarise',
      kind: 'agent',
      brief: 'Summarise {{steps.fetch.dir}} ({{steps.fetch.outputs.count}} pages) in a {{inputs.tone}} tone.',
      skills: ['web-research'],
      output_keys: [{ key: 'headline', description: 'The biggest change' }],
    },
    { id: 'approve', title: 'Publish?', kind: 'ask', question: 'Publish "{{steps.summarise.outputs.headline}}"?' },
    { id: 'archive', title: 'Archive', kind: 'automation', automation: 'archive', inputs: { folder: '{{steps.summarise.dir}}' } },
    { id: 'pause', title: 'Wait', kind: 'wait', minutes: 30 },
    { id: 'tell', title: 'Tell Desk', kind: 'tell_desk', text: 'Rejected: {{steps.approve.outputs.note}} ({{run.date}})' },
  ],
  edges: [
    { from: 'fetch', to: 'summarise', route: 'changed' },
    { from: 'summarise', to: 'approve', when: 'steps.summarise.outputs.headline != null and exists(inputs.tone)' },
    { from: 'approve', to: 'archive' },
    { from: 'approve', to: 'tell', route: 'rejected' },
    { from: 'archive', to: 'pause' },
  ],
  output_step: 'summarise',
});

function context(over: Partial<ValidateContext> = {}, automations: Record<string, AutomationDefinition> = { archive }): ValidateContext {
  return {
    projectId: 'p1',
    name: 'digest',
    skill: (name) => {
      if (name === 'mine') return { ok: true, hasScript: (p) => p === 'fetch.py' || p === 'scripts/fetch.py', builtinOff: false };
      if (name === 'web-research') return { ok: true, hasScript: () => false, builtinOff: false };
      if (name === 'pdf-toolkit') return { ok: true, hasScript: () => true, builtinOff: true };
      return { ok: false, reason: `No skill named '${name}'` };
    },
    model: (model, effort) => (model && model !== 'fake-model' ? `Unknown model '${model}'` : effort === 'max' ? "fake-model does not support reasoning effort 'max'" : null),
    gitSource: (id) => id === 'src1',
    automation: (n) => automations[n] ?? null,
    now: NOW,
    ...over,
  };
}

const check = (change: (d: any) => void, ctx = context()) => {
  const d = base();
  change(d);
  return validateDefinition(d, ctx);
};

describe('validateDefinition', () => {
  it('accepts a valid definition, warning only about a route no edge takes', () => {
    const r = validateDefinition(base(), context());
    expect(r.errors).toEqual([]);
    expect(r.warnings).toEqual([{ path: 'steps[0].routes[1]', message: "No edge takes the route 'nothing_new': when 'fetch' chooses it, that branch ends" }]);
    expect(r.definition).toMatchObject({ after_run: 'notify', limits: { max_parallel_agents: 2 } });
  });

  it('maps shape errors to field paths and stops there', () => {
    const r = check((d) => {
      d.steps[2].question = '';
      d.edges[0].route = 'Changed';
      d.inputs[1].key = '1tone';
    });
    expect(r.definition).toBeNull();
    expect(r.warnings).toEqual([]);
    expect(r.errors.map((e) => e.path)).toEqual(['inputs[1].key', 'steps[2].question', 'edges[0].route']);
    expect(validateDefinition('nope', context()).errors).toEqual([{ path: '', message: expect.stringContaining('expected object') }]);
    expect(issuePath(['triggers', 0, 'inputs', 'Bad'])).toBe('triggers[0].inputs.Bad');
    expect(issuePath([])).toBe('');
  });

  it('needs at least one step', () => {
    const r = check((d) => {
      d.steps = [];
      d.edges = [];
      delete d.output_step;
    });
    expect(r.errors).toEqual([{ path: 'steps', message: 'Add at least one step' }]);
  });

  it('refuses duplicate step ids and input keys', () => {
    const r = check((d) => {
      d.steps.push({ id: 'pause', title: 'Again', kind: 'wait', minutes: 5 });
      d.inputs.push({ key: 'tone', label: 'Tone again', type: 'text' });
    });
    expect(r.errors).toEqual([
      { path: 'steps[6].id', message: "Another step already has the id 'pause'" },
      { path: 'inputs[2].key', message: "Another input already has the key 'tone'" },
    ]);
  });

  it('refuses edges to unknown steps, self-loops and duplicate edges', () => {
    const r = check((d) => {
      d.edges.push({ from: 'ghost', to: 'tell' }, { from: 'tell', to: 'nobody' }, { from: 'pause', to: 'pause' }, { from: 'archive', to: 'pause' });
    });
    expect(r.errors).toEqual([
      { path: 'edges[5].from', message: "No step has the id 'ghost'" },
      { path: 'edges[6].to', message: "No step has the id 'nobody'" },
      { path: 'edges[7]', message: "An edge cannot lead from 'pause' to itself" },
      { path: 'edges[8]', message: 'Same edge as edges[4]' },
    ]);
  });

  it('refuses a cycle', () => {
    const r = check((d) => d.edges.push({ from: 'pause', to: 'fetch' }));
    expect(r.errors).toEqual([{ path: 'edges', message: expect.stringContaining('cycle') }]);
  });

  it('checks edge routes against the source step', () => {
    const r = check((d) => {
      d.edges[0].route = 'maybe';
      d.edges.push({ from: 'summarise', to: 'tell', route: 'error' }, { from: 'fetch', to: 'tell', route: 'rejected' }, { from: 'pause', to: 'tell', route: 'done' });
    });
    expect(r.errors).toEqual([
      { path: 'edges[0].route', message: "'fetch' has no route 'maybe' (its routes: changed, nothing_new)" },
      { path: 'edges[5].route', message: "Route 'error' needs on_error: continue on 'summarise'" },
      { path: 'edges[6].route', message: "Only Ask me steps take the route 'rejected' ('fetch' is a script step)" },
      { path: 'edges[7].route', message: "'pause' has no route 'done' (it declares none)" },
    ]);
    const ok = check((d) => {
      d.steps[1].on_error = 'continue';
      d.edges.push({ from: 'summarise', to: 'tell', route: 'error' });
    });
    expect(ok.errors).toEqual([]);
  });

  it('parses conditions and checks what they read', () => {
    expect(check((d) => (d.edges[1].when = 'steps.summarise.outputs.headline ==')).errors).toEqual([
      { path: 'edges[1].when', message: expect.stringContaining('position') },
    ]);
    const r = check((d) => {
      d.edges[1].when = 'inputs.nope == 1 or steps.approve.route != null';
      d.edges[2].when = 'steps.fetch.outputs.count > 0 and exists(steps.approve.outputs.note)'; // an ancestor, and the source itself
      d.edges[4].when = 'steps.summarise.outputs.missing == 1';
    });
    expect(r.errors).toEqual([
      { path: 'edges[1].when', message: "inputs.nope: no input has the key 'nope'" },
      { path: 'edges[1].when', message: "steps.approve.route: 'approve' is not upstream of 'summarise'" },
      { path: 'edges[4].when', message: "steps.summarise.outputs.missing: 'summarise' does not declare the output 'missing' (declared: headline)" },
    ]);
  });

  it('checks what templates read: inputs, upstream steps, never their own step', () => {
    const r = check((d) => {
      d.steps[1].brief = 'Use {{inputs.nope}}';
      d.steps[0].args = ['{{steps.summarise.summary}}'];
      d.steps[0].stdin = 'Sources: {{inputs.sources';
      d.steps[2].question = '{{steps.approve.summary}} {{steps.ghost.dir}}';
      d.steps[5].text = 'See {{steps.archive.dir}}'; // a parallel branch, not upstream
      d.steps[3].inputs.folder = '{{run.dir}}/x {{run.date}}';
    });
    expect(r.errors).toEqual([
      { path: 'steps[0].args[0]', message: "steps.summarise.summary: 'summarise' is not upstream of 'fetch'" },
      { path: 'steps[0].stdin', message: expect.any(String) },
      { path: 'steps[1].brief', message: "inputs.nope: no input has the key 'nope'" },
      { path: 'steps[2].question', message: 'steps.approve.summary: a step cannot read its own results' },
      { path: 'steps[2].question', message: "steps.ghost.dir: no step has the id 'ghost'" },
      { path: 'steps[5].text', message: "steps.archive.dir: 'archive' is not upstream of 'tell'" },
    ]);
  });

  it('applies the output rules of each step kind', () => {
    const r = check((d) => {
      d.steps.push({
        id: 'report',
        title: 'Report',
        kind: 'tell_desk',
        text: '{{steps.pause.outputs.x}} {{steps.tell.outputs.y}} {{steps.approve.outputs.decision}} {{steps.approve.outputs.note}} {{steps.fetch.outputs.anything}} {{steps.archive.outputs.run_id}} {{steps.summarise.outputs.body}}',
        join: 'any',
      });
      d.edges.push({ from: 'pause', to: 'report' }, { from: 'tell', to: 'report' });
    });
    expect(r.errors).toEqual([
      { path: 'steps[6].text', message: "steps.pause.outputs.x: 'pause' is a wait step and has no outputs" },
      { path: 'steps[6].text', message: "steps.tell.outputs.y: 'tell' is a Tell Desk step and has no outputs" },
      { path: 'steps[6].text', message: "steps.approve.outputs.decision: Ask me steps have only the output 'note'" },
      { path: 'steps[6].text', message: "steps.summarise.outputs.body: 'summarise' does not declare the output 'body' (declared: headline)" },
    ]);
    const none = check((d) => (d.steps[1].output_keys = []));
    expect(none.errors).toEqual([
      { path: 'edges[1].when', message: "steps.summarise.outputs.headline: 'summarise' does not declare the output 'headline' (it declares none)" },
      { path: 'steps[2].question', message: "steps.summarise.outputs.headline: 'summarise' does not declare the output 'headline' (it declares none)" },
    ]);
  });

  it('lets previous references name any step, but only a step that exists', () => {
    const r = check((d) => (d.steps[1].brief += ' {{previous.steps.tell.dir}} {{previous.steps.ghost.dir}}'));
    expect(r.errors).toEqual([{ path: 'steps[1].brief', message: "previous.steps.ghost.dir: no step has the id 'ghost'" }]);
  });

  it('checks script skills and their files', () => {
    expect(check((d) => (d.steps[0].skill = 'nope')).errors).toEqual([{ path: 'steps[0].skill', message: "No skill named 'nope'" }]);
    expect(check((d) => (d.steps[0].script = 'missing.py')).errors).toEqual([{ path: 'steps[0].script', message: "The skill 'mine' has no script 'missing.py'" }]);
    expect(check((d) => (d.steps[0].script = 'scripts/fetch.py')).errors).toEqual([]);
  });

  it('checks agent skills, models, efforts and git sources', () => {
    const calls: Array<[string, ReasoningEffort | undefined]> = [];
    const spy = context({ model: (m, e) => (calls.push([m, e]), null) });
    expect(validateDefinition(base(), spy).errors).toEqual([]);
    expect(calls).toEqual([]); // neither set: the project's thread settings apply
    check((d) => (d.steps[1].reasoning_effort = 'low'), spy);
    expect(calls).toEqual([['', 'low']]);
    const r = check((d) => {
      d.steps[1].skills = ['web-research', 'nope'];
      d.steps[1].model = 'gpt-9';
      d.steps[1].git_source_id = 'nope';
      d.steps[1].output_keys.push({ key: 'headline', description: 'again' });
    });
    expect(r.errors).toEqual([
      { path: 'steps[1].skills[1]', message: "No skill named 'nope'" },
      { path: 'steps[1].model', message: "Unknown model 'gpt-9'" },
      { path: 'steps[1].git_source_id', message: "'nope' is not a git source of this project" },
      { path: 'steps[1].output_keys[1].key', message: "The output key 'headline' is listed twice" },
    ]);
    expect(check((d) => (d.steps[1].reasoning_effort = 'max')).errors).toEqual([
      { path: 'steps[1].reasoning_effort', message: "fake-model does not support reasoning effort 'max'" },
    ]);
    expect(check((d) => (d.steps[1].git_source_id = 'src1')).errors).toEqual([]);
  });

  it('warns about built-in skills that are turned off', () => {
    const r = check((d) => {
      d.steps[0].skill = 'pdf-toolkit';
      d.steps[1].skills = ['pdf-toolkit'];
    });
    expect(r.errors).toEqual([]);
    expect(r.warnings.map((w) => w.path)).toEqual(['steps[0].skill', 'steps[1].skills[0]', 'steps[0].routes[1]']);
    expect(r.warnings[0]!.message).toContain('turned off');
  });

  it('checks inputs: choice options and default types', () => {
    const r = check((d) => {
      d.inputs[1].default = 'medium';
      d.inputs.push(
        { key: 'n', label: 'N', type: 'number', default: 'three' },
        { key: 'c', label: 'C', type: 'choice' },
        { key: 'flag', label: 'Flag', type: 'boolean', default: 'yes' },
        { key: 'u', label: 'U', type: 'url', default: 3 },
      );
    });
    expect(r.errors).toEqual([
      { path: 'inputs[1].default', message: "'medium' is not an option of 'tone' (short, long)" },
      { path: 'inputs[2].default', message: "'n' is a number input: use a number" },
      { path: 'inputs[3].options', message: "'c' is a choice input: give it options" },
      { path: 'inputs[4].default', message: "'flag' is a boolean input: use a boolean" },
      { path: 'inputs[5].default', message: "'u' is a url input: use a string" },
    ]);
  });

  it('checks schedules: frequency, timezone, inputs, and required inputs without defaults', () => {
    const r = check((d) => {
      d.triggers[0].cron = '*/2 * * * *';
      d.triggers.push(
        { kind: 'schedule', cron: '0 8 * * *', timezone: 'Mars/Olympus', inputs: { sources: 'x' } },
        { kind: 'schedule', cron: '0 9 * * *', timezone: 'UTC', inputs: { sources: 'x', nope: 1, tone: 'medium' } },
        { kind: 'schedule', cron: '0 10 * * *', timezone: 'UTC' },
      );
    });
    expect(r.errors).toEqual([
      { path: 'triggers[0].cron', message: "Runs at most every 5 minutes: '*/2 * * * *' runs every 2 minutes" },
      { path: 'triggers[1].timezone', message: "Unknown timezone 'Mars/Olympus': use an IANA name such as Europe/Paris" },
      { path: 'triggers[2].inputs.nope', message: "No input has the key 'nope'" },
      { path: 'triggers[2].inputs.tone', message: "'medium' is not an option of 'tone' (short, long)" },
      { path: 'triggers[3].inputs', message: "'sources' is required and has no default: give it a value for this schedule" },
    ]);
  });

  it('checks sub-automations: existence, input keys, required inputs', () => {
    expect(check((d) => (d.steps[3].automation = 'nope')).errors).toEqual([{ path: 'steps[3].automation', message: "No automation named 'nope' in this project" }]);
    expect(check((d) => (d.steps[3].inputs.colour = 'red')).errors).toEqual([{ path: 'steps[3].inputs.colour', message: "'archive' has no input 'colour'" }]);
    expect(check((d) => (d.steps[3].inputs = { label: 'x' })).errors).toEqual([{ path: 'steps[3].inputs', message: "'archive' needs the input 'folder'" }]);
  });

  it('refuses loops across automations, including running itself', () => {
    expect(check((d) => (d.steps[3].automation = 'digest')).errors).toEqual([
      { path: 'steps[3].automation', message: 'Automations would run each other in a loop: digest → digest' },
    ]);
    const callsBack = def({ ...archive, steps: [{ id: 'back', title: 'Back', kind: 'automation', automation: 'digest', inputs: {} }] });
    expect(check(() => {}, context({}, { archive: callsBack })).errors).toEqual([
      { path: 'steps[3].automation', message: 'Automations would run each other in a loop: digest → archive → digest' },
    ]);
    const calls = (to: string) => def({ title: to, steps: [{ id: 's', title: 's', kind: 'automation', automation: to }] });
    const lookup = (m: Record<string, AutomationDefinition>) => (n: string) => m[n] ?? null;
    expect(subAutomationCycle('a', calls('b'), lookup({ b: calls('c'), c: def({ title: 'c', steps: [wait('w')] }) }))).toBeNull();
    expect(subAutomationCycle('a', calls('b'), lookup({ b: calls('c'), c: calls('a') }))).toEqual(['a', 'b', 'c', 'a']);
    expect(subAutomationCycle('a', calls('ghost'), lookup({}))).toBeNull();
  });

  it('checks output_step', () => {
    expect(check((d) => (d.output_step = 'ghost')).errors).toEqual([{ path: 'output_step', message: "No step has the id 'ghost'" }]);
  });

  it('warns about a step without edges in a graph of several steps', () => {
    const r = check((d) => d.steps.push(wait('lonely')));
    expect(r.errors).toEqual([]);
    expect(r.warnings).toContainEqual({ path: 'steps[6]', message: "'lonely' has no edges: it runs on its own when the run starts" });
    const single = validateDefinition({ title: 'One', steps: [wait('only')] }, context());
    expect(single).toMatchObject({ errors: [], warnings: [] });
  });
});
