import { fireEvent, render, screen, within } from '@testing-library/angular';
import { describe, expect, it } from 'vitest';
import type { SkillNode } from '@desk/client';
import { SkillsMapView } from './skills-map-view';

const node = (key: string, extra: Partial<SkillNode> = {}): SkillNode => {
  const [scope, a, b] = key.split(':');
  return { key, name: b ?? a!, scope: scope as 'global' | 'project', projectId: b ? a! : null, projectName: null, version: 1, description: '', shadowedIn: [], shadows: false, usedBy: [], ...extra };
};
const t1 = { threadId: 't1', title: 'Welcome emails', status: 'running' as const, projectId: 'p1' };

async function setup() {
  const picked: string[] = [];
  await render(`<div deskSkillsMapView [nodes]="nodes" [projects]="projects" [catalogKeys]="keys" [selected]="selected" (selectSkill)="picked.push($event)"></div>`, {
    imports: [SkillsMapView],
    componentProperties: {
      nodes: [
        node('global:email-sequence', { version: 2, usedBy: [t1] }),
        node('global:brand-voice', { shadowedIn: ['p1'] }),
        node('project:p1:brand-voice', { projectName: 'Onboarding', version: 3, shadows: true, usedBy: [t1] }),
        node('project:p2:receipts', { projectName: 'Tax' }),
      ],
      projects: [
        { id: 'p1', name: 'Onboarding', tone: 'running' },
        { id: 'p2', name: 'Tax', tone: 'waiting' },
        { id: 'p3', name: 'Notes', tone: 'idle' },
      ],
      keys: new Set(['project:p2:receipts']),
      selected: 'global:brand-voice',
      picked,
    },
  });
  return { picked, map: screen.getByRole('group', { name: 'Skill map' }) };
}

describe('SkillsMapView', () => {
  it('puts every skill on the map as a button named for what it is', async () => {
    const { map, picked } = await setup();
    expect(map.className).toBe('map-canvas');
    const email = within(map).getByRole('button', { name: 'email-sequence, global, version 2, used by 1' });
    expect(email.className).toBe('skill-node global');
    expect(email.textContent).toBe('email-sequencev2');
    const brand = within(map).getByRole('button', { name: 'brand-voice, global, version 1, shadowed' });
    expect(brand.className).toBe('skill-node global shadowed selected');
    expect(brand.getAttribute('aria-pressed')).toBe('true');
    expect(within(map).getByRole('button', { name: 'brand-voice, Onboarding project skill, version 3, used by 1' }).className).toBe('skill-node project');
    expect(within(map).getByRole('button', { name: 'receipts, Tax project skill, version 1, from the catalog' }).className).toBe('skill-node project from-catalog');
    expect(email.style.width).toBe(email.style.height);
    fireEvent.click(email);
    expect(picked).toEqual(['global:email-sequence']);
  });

  it('draws the ring, the territories, the shadow link and the live threads with their lines', async () => {
    const { map } = await setup();
    const svg = map.querySelector('svg.skills-svg')!;
    expect(svg.getAttribute('aria-hidden')).toBe('true');
    expect([svg.getAttribute('width'), svg.getAttribute('height')]).toEqual(['1000', '700']);
    expect(svg.querySelectorAll('circle')).toHaveLength(4);
    expect(svg.querySelectorAll('path[stroke-dasharray="5 5"]')).toHaveLength(1);
    expect(svg.querySelectorAll('path[stroke="var(--run)"]')).toHaveLength(2);
    expect([...map.querySelectorAll('.skills-territory-label')].map((l) => l.textContent)).toEqual(['Onboarding', 'Tax', 'Notes · no project skills']);
    expect((map.querySelector('.skills-territory-label') as HTMLElement).style.color).not.toBe('');
    expect(map.querySelector('.skills-global-label')?.textContent).toBe('GLOBAL');
    expect(map.querySelector('.skills-shadow-label')?.textContent).toBe('shadowed by');
    const marker = within(map).getByRole('link', { name: 'Welcome emails' });
    expect(marker.getAttribute('href')).toBe('#/p/p1/threads/t1');
    expect(marker.className).toBe('skill-marker status-running');
  });
});
