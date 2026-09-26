import { TestBed } from '@angular/core/testing';
import { fireEvent, render, screen, waitFor, within } from '@testing-library/angular';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { initialGlobalState } from '@desk/bff/contract';
import { ToastService } from '../components/toast';
import { FakeDeskBridge, provideGlobal } from '../testing/fake-bridge';
import { AskDesk } from './ask-desk';

beforeEach(() => {
  window.location.hash = '#/skills';
});

describe('AskDesk', () => {
  it("asks the chosen project's Desk for a new skill, then opens that conversation", async () => {
    const bridge = new FakeDeskBridge({ 'projects.send': () => ({ ok: true }) });
    const closed = vi.fn();
    await render(AskDesk, { inputs: { projects: [{ id: 'p1', name: 'Onboarding' }, { id: 'p2', name: 'Tax' }] }, on: { close: closed }, providers: [...bridge.providers, provideGlobal(initialGlobalState())] });
    const sheet = await screen.findByRole('dialog', { name: 'Ask Desk for a new skill' });
    const project = within(sheet).getByLabelText("Which project's Desk") as HTMLSelectElement;
    expect(project.value).toBe('p1');
    expect(within(sheet).getByText('Desk has a thread draft and test it, then installs it.')).toBeTruthy();
    const text = within(sheet).getByLabelText('Message') as HTMLTextAreaElement;
    expect(text.value).toBe('Build a new skill that ');
    const send = within(sheet).getByRole('button', { name: 'Send to Desk' }) as HTMLButtonElement;
    fireEvent.input(text, { target: { value: 'Too short' } });
    expect(send.disabled).toBe(true);
    fireEvent.change(project, { target: { value: 'p2' } });
    fireEvent.input(text, { target: { value: '  Build a new skill that files receipts  ' } });
    expect(send.disabled).toBe(false);
    fireEvent.click(send);
    await waitFor(() => expect(window.location.hash).toBe('#/p/p2/conversation'));
    expect(bridge.calls).toEqual([{ channel: 'projects.send', input: { id: 'p2', text: 'Build a new skill that files receipts' } }]);
    expect(closed).toHaveBeenCalledTimes(1);
    expect(TestBed.inject(ToastService).list().map((t) => t.message)).toEqual(['Sent to Desk.']);
  });

  it('refines a named skill, and needs a project first', async () => {
    await render(AskDesk, { inputs: { skillName: 'brand-voice', projects: [] }, providers: [...new FakeDeskBridge().providers, provideGlobal(initialGlobalState())] });
    const sheet = await screen.findByRole('dialog', { name: 'Refine brand-voice with Desk' });
    expect(sheet.textContent).toContain('Create a project first; Desk works on skills from inside a project.');
    expect(within(sheet).queryByLabelText('Message')).toBeNull();
    expect((within(sheet).getByRole('button', { name: 'Send to Desk' }) as HTMLButtonElement).disabled).toBe(true);
  });
});
