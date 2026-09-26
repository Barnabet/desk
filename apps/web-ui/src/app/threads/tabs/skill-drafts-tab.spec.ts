import { TestBed } from '@angular/core/testing';
import { fireEvent, render, screen, waitFor } from '@testing-library/angular';
import { describe, expect, it, vi } from 'vitest';
import { ToastService } from '../../components/toast';
import { FakeDeskBridge } from '../../testing/fake-bridge';
import { SkillDraftsTab } from './skill-drafts-tab';

const TEMPLATE = `<div deskSkillDraftsTab projectId="p" threadTitle="Welcome emails" [drafts]="drafts" (browse)="browse($event)"></div>`;

describe('SkillDraftsTab', () => {
  it('explains skill drafts when the thread submitted none', async () => {
    await render(TEMPLATE, { imports: [SkillDraftsTab], componentProperties: { drafts: [], browse: vi.fn() }, providers: new FakeDeskBridge().providers });
    expect(screen.getByRole('heading', { name: 'No skill drafts' })).toBeTruthy();
    expect(screen.getByText(/A thread can package what it learned as a skill draft/)).toBeTruthy();
  });

  it('browses a draft, and asks Desk to review and install it', async () => {
    const bridge = new FakeDeskBridge({ 'projects.send': () => ({ ok: true }) });
    const browse = vi.fn();
    await render(TEMPLATE, { imports: [SkillDraftsTab], componentProperties: { drafts: ['drafts/email-sequence'], browse }, providers: bridge.providers });
    expect(document.querySelector('ul.tab-body.drafts > li.draft .mono')!.textContent).toBe('drafts/email-sequence');
    fireEvent.click(screen.getByRole('button', { name: 'View files' }));
    expect(browse).toHaveBeenCalledWith('drafts/email-sequence');
    fireEvent.click(screen.getByRole('button', { name: 'Ask Desk to review and install' }));
    await waitFor(() =>
      expect(bridge.calls).toEqual([
        { channel: 'projects.send', input: { id: 'p', text: `Please review the skill draft at drafts/email-sequence from the thread "Welcome emails" and install it if it's good.` } },
      ]),
    );
    await waitFor(() => expect(TestBed.inject(ToastService).list().map((t) => t.message)).toEqual(['Asked Desk to review the draft.']));
  });
});
