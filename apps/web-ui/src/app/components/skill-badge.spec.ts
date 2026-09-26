import { render, screen } from '@testing-library/angular';
import { describe, expect, it } from 'vitest';
import { SkillBadge } from './skill-badge';

describe('SkillBadge', () => {
  it("shows the skill's name, and whose skill it is when told", async () => {
    await render(`<span deskSkillBadge name="brand-voice"></span><span deskSkillBadge name="pdf-toolkit" scope="global"></span>`, { imports: [SkillBadge] });
    const plain = screen.getByText('brand-voice');
    const global = screen.getByText('pdf-toolkit');
    expect(plain.className).toBe('skill-badge');
    expect(plain.hasAttribute('title')).toBe(false);
    expect(global.className).toBe('skill-badge');
    expect(global.getAttribute('title')).toBe('global skill');
  });
});
