import { fireEvent, render, screen, within } from '@testing-library/angular';
import { describe, expect, it } from 'vitest';
import type { SkillNode } from '@desk/client';
import { SkillList } from './skill-list';

const node = (key: string, extra: Partial<SkillNode> = {}): SkillNode => {
  const [scope, a, b] = key.split(':');
  return { key, name: b ?? a!, scope: scope as 'global' | 'project', projectId: b ? a! : null, projectName: null, version: 1, description: '', shadowedIn: [], shadows: false, usedBy: [], ...extra };
};
const t1 = { threadId: 't1', title: 'Welcome emails', status: 'running' as const, projectId: 'p1' };

async function setup(nodes: SkillNode[], selected: string | null = null) {
  const picked: string[] = [];
  await render(`<div deskSkillList [nodes]="nodes" [projectNames]="names" [catalogKeys]="keys" [selected]="selected" (selectSkill)="picked.push($event)"></div>`, {
    imports: [SkillList],
    componentProperties: { nodes, names: new Map([['p1', 'Onboarding'], ['p2', 'Tax']]), keys: new Set(['global:pdf-toolkit']), selected, picked },
  });
  return { picked };
}

describe('SkillList', () => {
  it("lists global skills, then each project's own, sorted, with their chips, and picks one", async () => {
    const { picked } = await setup(
      [
        node('global:pdf-toolkit', { description: 'Read and fill PDFs' }),
        node('global:brand-voice', { description: 'House tone', shadowedIn: ['p1'], usedBy: [t1] }),
        node('project:p1:brand-voice', { projectName: 'Onboarding', version: 3, shadows: true, error: 'SKILL.md has no description' }),
      ],
      'global:brand-voice',
    );
    expect(document.querySelector('.skill-list')).toBeTruthy();
    expect(screen.getAllByRole('region').map((r) => r.getAttribute('aria-label'))).toEqual(['Global skills', 'Onboarding skills']);
    const global = screen.getByRole('region', { name: 'Global skills' });
    expect(global.querySelector('h2.skill-list-title')?.textContent).toBe('Global');
    expect([...global.querySelectorAll('.skill-row-name')].map((n) => n.textContent)).toEqual(['brand-voice', 'pdf-toolkit']);
    const brand = within(global).getByRole('button', { name: /^brand-voice/ });
    expect(brand.className).toBe('skill-row current');
    expect(brand.getAttribute('aria-pressed')).toBe('true');
    expect([...brand.querySelectorAll('.chip')].map((c) => c.textContent)).toEqual(['shadowed in 1', 'in use · 1']);
    const pdf = within(global).getByRole('button', { name: /^pdf-toolkit/ });
    expect(pdf.getAttribute('aria-pressed')).toBe('false');
    expect([...pdf.querySelectorAll('.chip')].map((c) => [c.className, c.textContent])).toEqual([['chip chip-done', 'from catalog']]);
    expect(pdf.textContent).toContain('Read and fill PDFs');
    const onboarding = screen.getByRole('region', { name: 'Onboarding skills' });
    const own = within(onboarding).getByRole('button');
    expect(own.textContent).toContain('Broken: SKILL.md has no description');
    expect(own.textContent).toContain('shadows global');
    expect(own.querySelector('.mono.small.muted')?.textContent).toBe('v3');
    fireEvent.click(own);
    expect(picked).toEqual(['project:p1:brand-voice']);
  });

  it('says when there are no global skills', async () => {
    await setup([node('project:p2:receipts')]);
    expect(within(screen.getByRole('region', { name: 'Global skills' })).getByText('No global skills yet.')).toBeTruthy();
    expect(screen.getByRole('region', { name: 'Tax skills' }).textContent).toContain('receipts');
  });
});
