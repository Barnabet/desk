import { ChangeDetectionStrategy, Component, signal } from '@angular/core';
import { fireEvent, render, screen } from '@testing-library/angular';
import { describe, expect, it } from 'vitest';
import { templateSuggestions } from '@desk/ui-core';
import { digestDef } from '@desk/ui-core/testing';
import { TemplateField } from './template-field';

@Component({
  selector: 'desk-template-harness',
  imports: [TemplateField],
  changeDetection: ChangeDetectionStrategy.OnPush,
  template: `<div deskTemplateField id="brief" label="Brief" [value]="v()" (valueChange)="v.set($event)" [suggestions]="suggestions" [multiline]="true"></div>`,
})
class Harness {
  readonly v = signal('');
  readonly suggestions = templateSuggestions(digestDef(), 'ok');
}

describe('TemplateField', () => {
  it('suggests paths after {{, inserts the chosen one, and marks paths not available here', async () => {
    await render(Harness);
    const box = screen.getByLabelText('Brief') as HTMLTextAreaElement;
    fireEvent.input(box, { target: { value: 'Publish {{steps.sum.o' } });
    fireEvent.mouseDown(await screen.findByRole('option', { name: /steps\.sum\.outputs\.headline/ }));
    expect(box.value).toBe('Publish {{steps.sum.outputs.headline}}');
    expect(screen.queryByRole('listbox')).toBeNull();
    expect(screen.getByText('{{steps.sum.outputs.headline}}').className).toBe('auto-tpl');
    fireEvent.input(box, { target: { value: 'x {{inputs.nope}}' } });
    expect(screen.getByText('{{inputs.nope}}').className).toContain('unknown');
  });

  it('moves through suggestions with the arrow keys and picks with Enter', async () => {
    await render(Harness);
    const box = screen.getByLabelText('Brief') as HTMLTextAreaElement;
    fireEvent.input(box, { target: { value: '{{run.' } });
    const options = await screen.findAllByRole('option');
    expect(options[0]!.getAttribute('aria-selected')).toBe('true');
    fireEvent.keyDown(box, { key: 'ArrowDown' });
    const second = screen.getAllByRole('option')[1]!;
    expect(second.getAttribute('aria-selected')).toBe('true');
    const path = second.querySelector('.mono')!.textContent!;
    fireEvent.keyDown(box, { key: 'Enter' });
    expect(box.value).toBe(`{{${path}}}`);
  });
});
