// @vitest-environment jsdom
import { useState } from 'react';
import { cleanup, fireEvent, render, screen } from '@testing-library/react';
import { afterEach, describe, expect, it } from 'vitest';
import { templateSuggestions } from '@desk/ui-core';
import { digestDef } from '@desk/ui-core/testing';
import { TemplateField } from './TemplateField';

afterEach(cleanup);

function Harness() {
  const [v, setV] = useState('');
  return <TemplateField id="brief" label="Brief" value={v} onChange={setV} suggestions={templateSuggestions(digestDef(), 'ok')} multiline />;
}

describe('TemplateField', () => {
  it('suggests paths after {{, inserts the chosen one, and marks paths not available here', async () => {
    render(<Harness />);
    const box = screen.getByLabelText('Brief') as HTMLTextAreaElement;
    fireEvent.change(box, { target: { value: 'Publish {{steps.sum.o' } });
    fireEvent.mouseDown(await screen.findByRole('option', { name: /steps\.sum\.outputs\.headline/ }));
    expect(box.value).toBe('Publish {{steps.sum.outputs.headline}}');
    expect(screen.queryByRole('listbox')).toBeNull();
    expect(screen.getByText('{{steps.sum.outputs.headline}}').className).toBe('auto-tpl');
    fireEvent.change(box, { target: { value: 'x {{inputs.nope}}' } });
    expect(screen.getByText('{{inputs.nope}}').className).toContain('unknown');
  });

  it('moves through suggestions with the arrow keys and picks with Enter', async () => {
    render(<Harness />);
    const box = screen.getByLabelText('Brief') as HTMLTextAreaElement;
    fireEvent.change(box, { target: { value: '{{run.' } });
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
