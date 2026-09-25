import { render, screen } from '@testing-library/angular';
import { describe, expect, it } from 'vitest';
import { Button } from './button';

describe('Button', () => {
  it('is a real button with its variant, size and own classes', async () => {
    await render(`<button deskButton variant="primary" size="sm" class="wide">Save</button>`, { imports: [Button] });
    const b = screen.getByRole('button', { name: 'Save' }) as HTMLButtonElement;
    expect(b.className.split(' ').sort()).toEqual(['btn', 'btn-primary', 'btn-sm', 'wide']);
    expect(b.type).toBe('button');
    expect(b.disabled).toBe(false);
    expect(b.hasAttribute('aria-busy')).toBe(false);
  });

  it('is secondary by default, and pending disables it and marks it busy', async () => {
    const view = await render(`<button deskButton [pending]="pending">Start</button>`, { imports: [Button], componentProperties: { pending: false } });
    const b = screen.getByRole('button', { name: 'Start' }) as HTMLButtonElement;
    expect(b.classList.contains('btn-secondary')).toBe(true);
    await view.rerender({ componentProperties: { pending: true } });
    expect(b.disabled).toBe(true);
    expect(b.getAttribute('aria-busy')).toBe('true');
  });
});
