import { afterEach, describe, expect, it } from 'vitest';
import { call, text, tools, type ChatRequest, type FakeReply } from '@desk/fake-model';
import { getAgent, getDeskAgent, listThreads } from '../state/queries';
import { createHarness, FAKE_MODEL, newRuntime, type Harness } from '../testing/harness';

let h: Harness;
afterEach(async () => h?.cleanup());

const system = (r: ChatRequest) => String(r.messages[0]?.content ?? '');
const step = (r: ChatRequest) => r.messages.filter((m) => m.role === 'assistant').length;
const lastTool = (r: ChatRequest) => String([...r.messages].reverse().find((m) => m.role === 'tool')?.content ?? '');
const allText = (r: ChatRequest) => r.messages.map((m) => String(m.content ?? '')).join('\n');

const SKILL_MD = '---\nname: word-count\ndescription: Counts words in text. Use whenever a word count is needed.\n---\n\nPipe the text to scripts/count.py with skill_run (stdin); it prints the number of words.\n';
const COUNT_PY = 'import sys\nprint(len(sys.stdin.read().split()))\n';

function script(r: ChatRequest): FakeReply {
  const sys = system(r);
  const n = step(r);
  if (sys.startsWith('You are Desk') && sys.includes('"Automations"')) {
    if (n === 0) return tools(call('spawn_thread', { title: 'Build word-count skill', brief: 'Write and test a word-count skill draft.' }));
    if (n === 1) return tools(call('wait_for_threads', {}));
    if (n === 2) {
      const from = /Skill drafts to review and install \(skill_write from_dir\): (\S+)/.exec(allText(r))?.[1];
      if (!from) return text('No draft reported.');
      return tools(call('skill_write', { name: 'word-count', scope: 'global', from_dir: from, change_note: 'Installed the thread draft' }));
    }
    return text('Installed the word-count skill globally.');
  }
  if (sys.startsWith('You are Desk') && sys.includes('"Essays"')) {
    if (n === 0) return tools(call('skill_list', {}));
    if (n === 1) return tools(call('spawn_thread', { title: 'Count words', brief: 'Count the words in "one two three".', skills: ['word-count'] }));
    if (n === 2) return tools(call('wait_for_threads', {}));
    return text('Done: see the thread result.');
  }
  if (sys.includes('Your assignment: Build word-count skill')) {
    return [
      tools(call('write_file', { path: 'skill-drafts/word-count/SKILL.md', content: SKILL_MD })),
      tools(call('write_file', { path: 'skill-drafts/word-count/scripts/count.py', content: COUNT_PY })),
      tools(call('bash', { command: 'echo "a b c" | python3 skill-drafts/word-count/scripts/count.py' })),
      tools(call('complete', { summary: `Draft ready; test printed ${lastTool(r).split('\n')[1]}`, skill_drafts: ['skill-drafts/word-count'] })),
    ][n] ?? text('done');
  }
  if (sys.includes('Your assignment: Count words')) {
    if (!sys.includes('## Active skills') || !sys.includes('scripts/count.py')) return tools(call('complete', { summary: 'Skill missing from my context' }));
    if (n === 0) return tools(call('skill_run', { name: 'word-count', script: 'count.py', stdin: 'one two three' }));
    return tools(call('complete', { summary: `Word count: ${lastTool(r).split('\n')[1]}` }));
  }
  return text('?');
}

describe('skill authoring end to end', () => {
  it('a thread drafts a skill, Desk installs it globally, another project uses it at spawn', async () => {
    h = await createHarness({ script });
    const rt = newRuntime(h);
    const settings = {
      desk_model: FAKE_MODEL.id,
      thread_model: FAKE_MODEL.id,
      policy: [
        { tool: 'bash', action: 'allow' as const },
        { tool: 'skill_run', action: 'allow' as const },
      ],
    };
    const a = rt.createProject({ name: 'Automations', goal: 'Build automations', settings });
    rt.sendToDesk(a, 'Make me a reusable word-count automation.');
    await rt.whenIdle();

    const builder = listThreads(h.store.db, a)[0]!;
    expect(builder.result_summary).toBe('Draft ready; test printed 3');
    expect(rt.listSkills().map((s) => [s.name, s.scope, s.version])).toEqual([['word-count', 'global', 1]]);
    const installed = rt.getSkill('word-count');
    expect(installed.files.map((f) => f.path)).toEqual(['SKILL.md', 'scripts/count.py']);
    const saved = h.store.list({ types: ['skill.saved'] })[0]!;
    expect(saved.type === 'skill.saved' && saved.payload).toMatchObject({ origin: `agent:${getDeskAgent(h.store.db, a)!.id}`, change_note: 'Installed the thread draft' });

    const b = rt.createProject({ name: 'Essays', goal: 'Write essays', settings });
    rt.sendToDesk(b, 'How many words are in "one two three"?');
    await rt.whenIdle();
    const counter = listThreads(h.store.db, b)[0]!;
    expect(getAgent(h.store.db, counter.id)!.active_skills).toEqual(['word-count']);
    expect(counter.result_summary).toBe('Word count: 3');
    expect(getAgent(h.store.db, getDeskAgent(h.store.db, b)!.id)!.status).toBe('idle');
    await rt.shutdown();
  });
});
