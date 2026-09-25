import { render, screen, within } from '@testing-library/angular';
import { describe, expect, it } from 'vitest';
import { ProjectNav } from './project-nav';

describe('ProjectNav', () => {
  it("links the project's five tabs and marks the open one", async () => {
    await render(`<nav deskProjectNav [projectId]="'p 1'" tab="memory"></nav>`, { imports: [ProjectNav] });
    const nav = screen.getByRole('navigation', { name: 'Project' });
    expect(nav.classList.contains('subnav')).toBe(true);
    const links = within(nav).getAllByRole('link');
    expect(links.map((a) => [a.textContent, a.getAttribute('href')])).toEqual([
      ['Conversation', '#/p/p%201/conversation'],
      ['Threads', '#/p/p%201/threads'],
      ['Library', '#/p/p%201/library'],
      ['Memory', '#/p/p%201/memory'],
      ['Settings', '#/p/p%201/settings'],
    ]);
    expect(links.filter((a) => a.getAttribute('aria-current') === 'page').map((a) => a.textContent)).toEqual(['Memory']);
  });
});
