import { describe, expect, it } from 'vitest';
import type { SkillNode } from '@desk/client';
import { layoutSkillsMap } from './skillsMap';

const node = (key: string, extra: Partial<SkillNode> = {}): SkillNode => {
  const [scope, a, b] = key.split(':');
  return { key, name: b ?? a!, scope: scope as 'global' | 'project', projectId: b ? a! : null, projectName: null, version: 1, description: '', shadowedIn: [], shadows: false, usedBy: [], ...extra };
};

describe('layoutSkillsMap', () => {
  it('rings globals, fills territories, links live threads and shadows', () => {
    const nodes = [
      node('global:email-sequence', { usedBy: [{ threadId: 't1', title: 'Welcome emails', status: 'running', projectId: 'p1' }] }),
      node('global:brand-voice', { shadowedIn: ['p1'] }),
      node('global:csv-analysis', { usedBy: [{ threadId: 't9', title: 'Old', status: 'done', projectId: 'p2' }] }),
      node('project:p1:brand-voice', { shadows: true, usedBy: [{ threadId: 't1', title: 'Welcome emails', status: 'running', projectId: 'p1' }] }),
      node('project:p2:receipts'),
    ];
    const l = layoutSkillsMap({ nodes, projects: [{ id: 'p1', name: 'Onboarding', tone: 'running' }, { id: 'p2', name: 'Tax', tone: 'waiting' }, { id: 'p3', name: 'Notes', tone: 'idle' }], width: 1100, height: 800 });
    expect(l.skills).toHaveLength(5);
    const g = l.skills.filter((s) => s.key.startsWith('global:'));
    for (const s of g) expect(Math.hypot(s.x - l.center.x, s.y - l.center.y)).toBeCloseTo(l.globalRadius, 5);
    expect(l.skills.find((s) => s.key === 'global:email-sequence')!.r).toBeGreaterThan(l.skills.find((s) => s.key === 'global:brand-voice')!.r);
    const p1 = l.territories.find((t) => t.projectId === 'p1')!;
    const own = l.skills.find((s) => s.key === 'project:p1:brand-voice')!;
    expect(Math.hypot(own.x - p1.x, own.y - p1.y)).toBeLessThan(p1.r);
    expect(l.territories.find((t) => t.projectId === 'p3')!.count).toBe(0);
    expect(l.markers).toHaveLength(1);
    expect(l.markers[0]).toMatchObject({ threadId: 't1', status: 'running' });
    expect(l.markers[0]!.to).toHaveLength(2);
    expect(l.shadows).toEqual([expect.objectContaining({ fromKey: 'global:brand-voice', toKey: 'project:p1:brand-voice' })]);
    for (const t of l.territories) {
      expect(t.x - t.r).toBeGreaterThanOrEqual(0);
      expect(t.x + t.r).toBeLessThanOrEqual(1100);
      expect(t.y + t.r).toBeLessThanOrEqual(800);
    }
  });
});
