import { TestBed } from '@angular/core/testing';
import { fireEvent, render, screen, waitFor } from '@testing-library/angular';
import { describe, expect, it, vi } from 'vitest';
import { MAX_UPLOAD } from '@desk/ui-core';
import { ToastService } from '../components/toast';
import { FakeDeskBridge, type FakeHandlers } from '../testing/fake-bridge';
import { Composer } from './composer';

/** Gives a File the `arrayBuffer()` that `fileToBase64` reads (through FileReader): web-ui's jsdom (27) has none. */
function readable(file: File): File {
  Object.defineProperty(file, 'arrayBuffer', {
    value: () =>
      new Promise<ArrayBuffer>((resolve, reject) => {
        const reader = new FileReader();
        reader.onload = () => resolve(reader.result as ArrayBuffer);
        reader.onerror = () => reject(reader.error);
        reader.readAsArrayBuffer(file);
      }),
  });
  return file;
}

async function setup(handlers: FakeHandlers = {}, draft = '') {
  const bridge = new FakeDeskBridge({ 'projects.send': () => ({ ok: true }), ...handlers });
  const sent = vi.fn();
  await render(`<div deskComposer projectId="p" [(draft)]="draft" (sent)="sent($event)"></div>`, {
    imports: [Composer],
    componentProperties: { draft, sent },
    providers: bridge.providers,
  });
  return { bridge, sent, box: screen.getByLabelText('Message Desk') as HTMLTextAreaElement, send: () => screen.getByRole('button', { name: 'Send' }) as HTMLButtonElement };
}

describe('Composer', () => {
  it('sends the trimmed draft with Enter, keeps Shift+Enter for a new line, and empties the box', async () => {
    const { bridge, sent, box, send } = await setup();
    expect(box.id).toBe('composer-p');
    expect(box.getAttribute('placeholder')).toBe('Brief a new piece of work, answer a question, or change direction');
    expect(send().disabled).toBe(true);
    fireEvent.input(box, { target: { value: '  Ship the checklist  ' } });
    expect(send().disabled).toBe(false);
    fireEvent.keyDown(box, { key: 'Enter', shiftKey: true });
    expect(bridge.calls).toEqual([]);
    fireEvent.keyDown(box, { key: 'Enter' });
    await waitFor(() => expect(box.value).toBe(''));
    expect(bridge.calls).toEqual([{ channel: 'projects.send', input: { id: 'p', text: 'Ship the checklist' } }]);
    expect(sent).toHaveBeenCalledWith('Ship the checklist');
    expect(screen.getByText('⏎ send · ⇧⏎ new line')).toBeTruthy();
  });

  it('attaches files through the Library and refuses ones over 25 MB', async () => {
    const { bridge, box } = await setup({ 'library.upload': ({ file }: { file: { name: string } }) => ({ id: 'a1', path: `uploads/${file.name}` }) }, 'See notes');
    expect(box.value).toBe('See notes');
    const big = new File(['x'], 'big.bin');
    Object.defineProperty(big, 'size', { value: MAX_UPLOAD + 1 });
    fireEvent.change(screen.getByTestId('attach-input'), { target: { files: [big, readable(new File(['hi'], 'notes.md'))] } });
    await waitFor(() => expect(box.value).toBe('See notes\nAttached: uploads/notes.md\n'));
    expect(bridge.calls).toEqual([{ channel: 'library.upload', input: { projectId: 'p', file: { name: 'notes.md', content_base64: 'aGk=' } } }]);
    expect(TestBed.inject(ToastService).list().map((t) => t.message)).toEqual(['big.bin is larger than 25 MB.']);
    expect((screen.getByRole('button', { name: 'Attach a file' }) as HTMLButtonElement).disabled).toBe(false);
  });

  it('starts a skill request in an empty box and puts the cursor there', async () => {
    const { box } = await setup();
    fireEvent.click(screen.getByRole('button', { name: 'Turn this into a skill' }));
    expect(box.value).toBe('Turn what we just did into a reusable skill: ');
    expect(document.activeElement).toBe(box);
    fireEvent.input(box, { target: { value: 'Keep this' } });
    fireEvent.click(screen.getByRole('button', { name: 'Turn this into a skill' }));
    expect(box.value).toBe('Keep this');
  });
});
