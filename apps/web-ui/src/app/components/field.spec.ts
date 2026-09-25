import { render, screen } from '@testing-library/angular';
import { describe, expect, it } from 'vitest';
import { Field } from './field';

describe('Field', () => {
  it('labels its control, and shows the error in place of the hint', async () => {
    const view = await render(`<div deskField id="project-name" label="Name" hint="What to call it" [error]="error"><input id="project-name" /></div>`, {
      imports: [Field],
      componentProperties: { error: null as string | null },
    });
    expect(screen.getByLabelText('Name').tagName).toBe('INPUT');
    expect(document.querySelectorAll('#project-name')).toHaveLength(1);
    expect(screen.getByText('What to call it').id).toBe('project-name-hint');
    await view.rerender({ componentProperties: { error: 'A project needs a name.' } });
    const alert = screen.getByRole('alert');
    expect(alert.textContent).toBe('A project needs a name.');
    expect(alert.id).toBe('project-name-error');
    expect(screen.queryByText('What to call it')).toBeNull();
  });
});
