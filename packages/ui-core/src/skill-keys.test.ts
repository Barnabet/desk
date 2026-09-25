import { describe, expect, it } from 'vitest';
import { parseSkillKey, skillKey } from './skill-keys';

describe('skill keys', () => {
  it('name global and project skills the way the Skills route does', () => {
    expect(skillKey({ scope: 'global', name: 'pdf-toolkit' })).toBe('global:pdf-toolkit');
    expect(skillKey({ scope: 'project', projectId: 'p1', name: 'brief' })).toBe('project:p1:brief');
  });

  it('parse back their own keys, and nothing else', () => {
    expect(parseSkillKey('global:pdf-toolkit')).toEqual({ scope: 'global', name: 'pdf-toolkit' });
    expect(parseSkillKey('project:p1:brief')).toEqual({ scope: 'project', projectId: 'p1', name: 'brief' });
    for (const key of ['', 'global:', 'global:a:b', 'project:p1', 'project::brief', 'catalog:x']) expect(parseSkillKey(key)).toBeNull();
  });
});
