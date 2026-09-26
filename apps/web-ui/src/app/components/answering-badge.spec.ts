import { render, screen } from '@testing-library/angular';
import { describe, expect, it } from 'vitest';
import { AnsweringBadge } from './answering-badge';

describe('AnsweringBadge', () => {
  it('reads "answering <asker>" after a live dot that screen readers skip', async () => {
    await render(`<span deskAnsweringBadge [label]="label"></span>`, { imports: [AnsweringBadge], componentProperties: { label: 'answering Frontend' } });
    const badge = screen.getByText('answering Frontend');
    expect(badge.tagName).toBe('SPAN');
    expect(badge.className).toBe('answering-badge');
    expect(badge.textContent).toBe('answering Frontend');
    expect([...badge.children].map((c) => [c.className, c.getAttribute('aria-hidden')])).toEqual([['live-dot', 'true']]);
  });
});
