import { fireEvent, render, screen } from '@testing-library/angular';
import { describe, expect, it, vi } from 'vitest';
import { ConfirmDialog } from './confirm-dialog';

describe('ConfirmDialog', () => {
  it('asks in a sheet, with Cancel and a confirm button that can be dangerous', async () => {
    const confirm = vi.fn();
    const cancel = vi.fn();
    const view = await render(
      `<div deskConfirmDialog title="Stop deskd?" confirmLabel="Stop" danger (confirm)="confirm()" (cancel)="cancel()"><p class="subtitle">Running threads pause.</p></div>`,
      { imports: [ConfirmDialog], componentProperties: { confirm, cancel } },
    );
    await view.fixture.whenStable();
    const dialog = screen.getByRole('dialog', { name: 'Stop deskd?' });
    expect(dialog.style.width).toBe('460px');
    expect(dialog.querySelector('.sheet-body')?.textContent).toBe('Running threads pause.');
    const buttons = [...dialog.querySelectorAll('.sheet-footer button')];
    expect(buttons.map((b) => b.textContent)).toEqual(['Cancel', 'Stop']);
    expect(buttons[1]!.classList.contains('btn-danger')).toBe(true);
    fireEvent.click(screen.getByRole('button', { name: 'Stop' }));
    expect(confirm).toHaveBeenCalledTimes(1);
    fireEvent.click(screen.getByRole('button', { name: 'Cancel' }));
    fireEvent.keyDown(window, { key: 'Escape' });
    expect(cancel).toHaveBeenCalledTimes(2);
  });
});
