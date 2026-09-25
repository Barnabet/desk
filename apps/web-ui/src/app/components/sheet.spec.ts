import { fireEvent, render, screen } from '@testing-library/angular';
import { describe, expect, it, vi } from 'vitest';
import { Sheet } from './sheet';

const TEMPLATE = `
  @if (open) {
    <div deskSheet title="New project" [width]="640" (close)="onClose()">
      <input aria-label="Name" />
      <div class="sheet-footer"><button type="button">Done</button></div>
    </div>
  }
`;

async function renderSheet() {
  const onClose = vi.fn();
  const before = document.createElement('button');
  document.body.append(before);
  before.focus();
  const view = await render(TEMPLATE, { imports: [Sheet], componentProperties: { open: true, onClose } });
  await view.fixture.whenStable();
  return { view, onClose, before };
}

describe('Sheet', () => {
  it('is a labelled modal dialog in the body, with the body, the footer and the focus inside', async () => {
    const { before } = await renderSheet();
    const dialog = screen.getByRole('dialog', { name: 'New project' });
    const backdrop = dialog.parentElement!;
    expect(backdrop.classList.contains('sheet-backdrop')).toBe(true);
    expect(backdrop.parentElement).toBe(document.body);
    expect(backdrop.hasAttribute('title')).toBe(false);
    expect(dialog.getAttribute('aria-modal')).toBe('true');
    expect(dialog.style.width).toBe('640px');
    expect([...dialog.children].map((c) => c.className)).toEqual(['sheet-title', 'sheet-body', 'sheet-footer']);
    expect(document.activeElement).toBe(screen.getByRole('textbox', { name: 'Name' }));
    before.remove();
  });

  it('closes on Escape and on a backdrop mousedown, not on one inside', async () => {
    const { onClose, before } = await renderSheet();
    const dialog = screen.getByRole('dialog', { name: 'New project' });
    fireEvent.keyDown(window, { key: 'Escape' });
    expect(onClose).toHaveBeenCalledTimes(1);
    fireEvent.mouseDown(dialog);
    expect(onClose).toHaveBeenCalledTimes(1);
    fireEvent.mouseDown(dialog.parentElement!);
    expect(onClose).toHaveBeenCalledTimes(2);
    before.remove();
  });

  it('leaves the body and gives the focus back when it closes', async () => {
    const { view, before } = await renderSheet();
    await view.rerender({ componentProperties: { open: false } });
    await view.fixture.whenStable();
    expect(document.querySelector('.sheet-backdrop')).toBeNull();
    expect(document.activeElement).toBe(before);
    before.remove();
  });
});
