import { TestBed } from '@angular/core/testing';
import { fireEvent, render, screen, waitFor, within } from '@testing-library/angular';
import { describe, expect, it, vi } from 'vitest';
import { initialGlobalState } from '@desk/bff/contract';
import type { SkillDetail } from '@desk/client';
import { MAX_UPLOAD, type SkillRef } from '@desk/ui-core';
import { ToastService } from '../components/toast';
import { FakeDeskBridge, provideGlobal, type FakeHandlers } from '../testing/fake-bridge';
import { SkillEditor } from './skill-editor';

async function setup(o: { skill?: { ref: SkillRef; detail: SkillDetail }; defaultProjectId?: string; handlers?: FakeHandlers } = {}) {
  const bridge = new FakeDeskBridge(o.handlers);
  const saved = vi.fn();
  const closed = vi.fn();
  await render(SkillEditor, {
    inputs: { skill: o.skill, projects: [{ id: 'p1', name: 'Onboarding' }, { id: 'p2', name: 'Tax' }], defaultProjectId: o.defaultProjectId },
    on: { saved, close: closed },
    providers: [...bridge.providers, provideGlobal(initialGlobalState())],
  });
  return { bridge, saved, closed, sheet: await screen.findByRole('dialog') };
}

describe('SkillEditor', () => {
  it('creates a project skill: checks the fields, uploads into a folder, adds a text file, and refuses a file over 25 MB', async () => {
    const { bridge, saved, sheet } = await setup({ defaultProjectId: 'p2', handlers: { 'skills.save': () => ({ version: 1 }) } });
    expect(sheet.querySelector('.sheet-title')?.textContent).toBe('New skill');
    expect((within(sheet).getByLabelText('Scope') as HTMLSelectElement).value).toBe('p2');
    expect(within(sheet).getByText('No files besides SKILL.md.')).toBeTruthy();
    fireEvent.click(within(sheet).getByRole('button', { name: 'Create skill' }));
    expect(within(sheet).getAllByRole('alert').map((a) => a.textContent)).toEqual([
      'Skill names use lowercase letters, digits and single hyphens (e.g. "weekly-report")',
      'Say in one line what the skill is for; Desk uses it to pick skills.',
      'Write the instructions agents follow.',
    ]);
    expect(bridge.calls).toEqual([]);

    fireEvent.input(within(sheet).getByLabelText('Name'), { target: { value: 'receipt-sorting' } });
    fireEvent.input(within(sheet).getByLabelText('Description'), { target: { value: 'Sort receipts' } });
    fireEvent.input(within(sheet).getByLabelText('Instructions (SKILL.md)'), { target: { value: 'Sort them.' } });
    fireEvent.input(within(sheet).getByLabelText('Folder for uploads'), { target: { value: 'data' } });
    const big = new File(['x'], 'big.bin');
    Object.defineProperty(big, 'size', { value: MAX_UPLOAD + 1 });
    fireEvent.change(within(sheet).getByTestId('skill-upload'), { target: { files: [new File(['hi'], 'sort.py'), big] } });
    expect(TestBed.inject(ToastService).list().map((t) => t.message)).toEqual(['big.bin is larger than 25 MB.']);
    const add = within(sheet).getByRole('button', { name: 'Add file' }) as HTMLButtonElement;
    expect(add.disabled).toBe(true);
    fireEvent.input(within(sheet).getByLabelText('New file path'), { target: { value: '/scripts/check.py' } });
    fireEvent.input(within(sheet).getByLabelText('New file content'), { target: { value: 'print(2)' } });
    fireEvent.click(add);
    expect((within(sheet).getByLabelText('New file path') as HTMLInputElement).value).toBe('');
    const rows = () => [...sheet.querySelectorAll('.skill-files-edit li')].map((li) => [li.className, li.querySelector('.mono')?.textContent, li.querySelector('.muted')?.textContent]);
    expect(rows()).toEqual([
      ['adding', 'data/sort.py', '2 B'],
      ['adding', 'scripts/check.py', 'new text file'],
    ]);
    fireEvent.click(within(sheet).getByRole('button', { name: "Don't add data/sort.py" }));
    expect(rows()).toEqual([['adding', 'scripts/check.py', 'new text file']]);

    fireEvent.click(within(sheet).getByRole('button', { name: 'Create skill' }));
    await waitFor(() => expect(saved).toHaveBeenCalledWith({ scope: 'project', projectId: 'p2', name: 'receipt-sorting' }));
    expect(bridge.calls).toEqual([
      {
        channel: 'skills.save',
        input: { projectId: 'p2', name: 'receipt-sorting', skill: { description: 'Sort receipts', instructions: 'Sort them.', files: [{ path: 'scripts/check.py', content_base64: 'cHJpbnQoMik=' }], remove_files: [] } },
      },
    ]);
  });

  it("refines a skill in place: only what changed, files to remove, a change note, and deskd's refusal in the form", async () => {
    const detail = { name: 'email-sequence', scope: 'global', description: 'Writes emails', dir: '/s/email-sequence', version: 2, instructions: 'Plan it.', frontmatter: {}, files: [{ path: 'SKILL.md', size: 40 }, { path: 'scripts/count.py', size: 12 }] } as SkillDetail;
    let refuse = true;
    const { bridge, saved, closed, sheet } = await setup({
      skill: { ref: { scope: 'global', name: 'email-sequence' }, detail },
      handlers: {
        'skills.save': () => {
          if (refuse) throw { code: 'invalid', message: 'scripts/count.py is still named in SKILL.md', status: 400 };
          return { version: 3 };
        },
      },
    });
    expect(sheet.querySelector('.sheet-title')?.textContent).toBe('Edit email-sequence');
    expect(within(sheet).queryByLabelText('Name')).toBeNull();
    expect((within(sheet).getByLabelText('Description') as HTMLInputElement).value).toBe('Writes emails');
    expect((within(sheet).getByLabelText('Instructions (SKILL.md)') as HTMLTextAreaElement).value).toBe('Plan it.');
    expect([...sheet.querySelectorAll('.skill-files-edit .mono')].map((m) => m.textContent)).toEqual(['scripts/count.py']);
    fireEvent.click(within(sheet).getByRole('checkbox'));
    expect(sheet.querySelector('.skill-files-edit li')?.className).toBe('removing');
    fireEvent.input(within(sheet).getByLabelText('Change note (optional)'), { target: { value: 'Tidy' } });
    fireEvent.click(within(sheet).getByRole('button', { name: 'Save new version' }));
    expect((await within(sheet).findByRole('alert')).textContent).toBe('scripts/count.py is still named in SKILL.md');
    expect(saved).not.toHaveBeenCalled();
    refuse = false;
    fireEvent.click(within(sheet).getByRole('button', { name: 'Save new version' }));
    await waitFor(() => expect(saved).toHaveBeenCalledWith({ scope: 'global', name: 'email-sequence' }));
    expect(bridge.calls.at(-1)?.input).toEqual({ name: 'email-sequence', skill: { files: [], remove_files: ['scripts/count.py'], change_note: 'Tidy' } });
    fireEvent.click(within(sheet).getByRole('button', { name: 'Cancel' }));
    expect(closed).toHaveBeenCalledTimes(1);
  });
});
