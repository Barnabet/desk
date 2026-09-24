import { describe, expect, it } from 'vitest';
import type { SkillSummary } from '../types';
import { buildSkillGraph } from './skills';

const sk = (name: string, scope: 'global' | 'project', version = 1): SkillSummary => ({ name, scope, description: `${name} desc`, dir: `/s/${name}`, version });

describe('buildSkillGraph', () => {
  it('links skills to projects, marks shadowing, and resolves thread usage like agents do', () => {
    const nodes = buildSkillGraph({
      global: [sk('email-sequence', 'global', 4), sk('brand-voice', 'global')],
      projects: [
        { id: 'p1', name: 'Onboarding', skills: [sk('brand-voice', 'project', 3), sk('email-sequence', 'global', 4)] },
        { id: 'p2', name: 'Tax', skills: [] },
      ],
      threads: [
        { id: 't1', title: 'Emails', status: 'running', projectId: 'p1', skills: ['brand-voice', 'email-sequence'] },
        { id: 't2', title: 'Old', status: 'done', projectId: 'p2', skills: ['brand-voice'] },
      ],
    });
    const by = (key: string) => nodes.find((n) => n.key === key)!;
    expect(nodes.map((n) => n.key).sort()).toEqual(['global:brand-voice', 'global:email-sequence', 'project:p1:brand-voice']);
    expect(by('global:brand-voice')).toMatchObject({ shadowedIn: ['p1'], usedBy: [{ threadId: 't2' }] });
    expect(by('project:p1:brand-voice')).toMatchObject({ shadows: true, projectName: 'Onboarding', version: 3, usedBy: [{ threadId: 't1', status: 'running' }] });
    expect(by('global:email-sequence').usedBy.map((u) => u.threadId)).toEqual(['t1']);
  });
});
