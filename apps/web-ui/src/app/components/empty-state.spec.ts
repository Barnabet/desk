import { render, screen } from '@testing-library/angular';
import { describe, expect, it } from 'vitest';
import { EmptyState } from './empty-state';

describe('EmptyState', () => {
  it('shows the title, the body and the action', async () => {
    await render(`<div deskEmptyState title="All clear" body="Approvals, questions and hand-offs land here."><a href="#/map">Back to the map</a></div>`, { imports: [EmptyState] });
    expect(screen.getByRole('heading', { name: 'All clear' })).toBeTruthy();
    expect(screen.getByText('Approvals, questions and hand-offs land here.').className).toBe('subtitle');
    expect(screen.getByRole('link', { name: 'Back to the map' }).getAttribute('href')).toBe('#/map');
    const root = document.querySelector('.empty')!;
    expect(root.hasAttribute('title')).toBe(false);
    expect([...root.children].map((c) => c.tagName)).toEqual(['H2', 'P', 'A']);
  });

  it('leaves the body out when there is none', async () => {
    await render(`<div deskEmptyState title="Couldn't load this project" [body]="null"></div>`, { imports: [EmptyState] });
    expect(document.querySelector('.empty .subtitle')).toBeNull();
  });
});
