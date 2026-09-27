import { describe, expect, it } from 'vitest';
import type { SkillSummary } from '@desk/client';
import type { BuiltinSkillInfo } from '@desk/protocol';
import { digestDef } from './testing/automations';
import { edgeRouteOptions, keyProblem, routeProblem, scriptFiles, skillChoices } from './automation-fields';

const skill = (name: string, scope: 'global' | 'project', error?: string) => ({ name, scope, description: '', dir: '/x', version: 1, ...(error ? { error } : {}) }) as SkillSummary;
const builtin = (name: string, enabled = true, broken: string | null = null) => ({ name, enabled, broken }) as BuiltinSkillInfo;

describe('automation fields', () => {
  it('lists skills once, a project skill over a global one over a built-in, without broken or disabled ones', () => {
    expect(skillChoices([skill('digest', 'project'), skill('bad', 'project', 'no SKILL.md')], [skill('digest', 'global'), skill('notes', 'global')], [builtin('pdf-toolkit'), builtin('images', false), builtin('audio-video', true, 'digest mismatch'), builtin('notes')])).toEqual([
      { name: 'digest', source: 'project' },
      { name: 'notes', source: 'global' },
      { name: 'pdf-toolkit', source: 'builtin' },
    ]);
  });

  it('lists a skill’s scripts, not its helpers or caches', () => {
    expect(scriptFiles([{ path: 'SKILL.md' }, { path: 'scripts/fetch.py' }, { path: 'scripts/_common.py' }, { path: 'scripts/__pycache__/x.py' }, { path: 'run.sh' }, { path: 'scripts/lib/.hidden.js' }, { path: 'packages.txt' }])).toEqual(['run.sh', 'scripts/fetch.py']);
  });

  it('checks route names and output keys', () => {
    expect(routeProblem('changed')).toBeNull();
    expect(routeProblem('error')).toMatch(/set by Desk/);
    expect(routeProblem('Bad Name')).toMatch(/lowercase/i);
    expect(keyProblem('headline')).toBeNull();
    expect(keyProblem('Head-line')).toMatch(/lowercase/i);
  });

  it('offers the routes an edge can wait for', () => {
    const def = digestDef();
    expect(edgeRouteOptions(def, 'fetch')).toEqual([
      { value: '', label: 'When it succeeds' },
      { value: 'changed', label: 'On route changed' },
      { value: 'unchanged', label: 'On route unchanged' },
    ]);
    expect(edgeRouteOptions(def, 'ok').map((o) => o.value)).toEqual(['', 'rejected']);
    const lenient = { ...def, steps: def.steps.map((s) => (s.id === 'sum' ? { ...s, on_error: 'continue' as const } : s)) };
    expect(edgeRouteOptions(lenient, 'sum', 'gone').map((o) => o.value)).toEqual(['', 'error', 'gone']);
  });
});
