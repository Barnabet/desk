// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it } from 'vitest';
import type { ProjectSummary } from '@desk/protocol';
import { initialGlobalState } from '../../shared/state';
import { useRoute } from '../router';
import { globalStore } from '../state/global';
import { installBridge } from '../test/bridge';
import { builtin } from '../test/builtins';
import { SkillsScreen } from './SkillsScreen';

afterEach(cleanup);
beforeEach(() => {
  window.location.hash = '#/skills';
  localStorage.clear();
  const summary = (id: string, name: string, threads: unknown[]) =>
    ({ project: { id, name, goal: '', updated_at: 't' }, desk_status: 'idle', threads, latest_report: null, plan_progress: { done: 0, total: 0 }, attention_count: 0 }) as unknown as ProjectSummary;
  globalStore.set({
    ...initialGlobalState(),
    connection: { status: 'live' },
    overview: [
      summary('p1', 'Onboarding', [{ id: 't1', title: 'Welcome emails', status: 'running', reason: null, activity: null, model: 'm', git_branch: null, skills: ['email-sequence', 'brand-voice'], review_round: 0, created_at: 't', updated_at: 't' }]),
      summary('p2', 'Tax', []),
    ],
  });
});

const sk = (name: string, scope: 'global' | 'project', version = 1, description = `${name} does things`) => ({ name, scope, description, dir: `/s/${name}`, version });
const lists: Record<string, unknown[]> = {
  global: [sk('email-sequence', 'global', 2), sk('brand-voice', 'global')],
  p1: [sk('brand-voice', 'project', 3, 'House tone'), sk('email-sequence', 'global', 2)],
  p2: [sk('email-sequence', 'global', 2), sk('brand-voice', 'global')],
};
const detail = (v: number, instructions: string) => ({ ...sk('email-sequence', 'global', v), instructions, frontmatter: {}, files: [{ path: 'SKILL.md', size: 40 }, { path: 'scripts/count.py', size: 12 }] });

function Routed() {
  const r = useRoute();
  return <SkillsScreen {...(r.name === 'skills' && r.skill ? { skill: r.skill } : {})} />;
}

function setup(extra: Record<string, (input: any) => unknown> = {}) {
  const bridge = installBridge({
    'skills.list': ({ projectId }: { projectId?: string }) => lists[projectId ?? 'global'],
    'skills.get': () => detail(2, 'Plan the sequence.\nCheck the voice.'),
    'skills.history': () => [
      { version: 1, description: 'd', current: false, change_note: 'First draft', origin: 'user', ts: new Date().toISOString() },
      { version: 2, description: 'd', current: true, change_note: 'Added a voice check', origin: 'agent:t1', ts: new Date().toISOString() },
    ],
    'skills.version': ({ version }: { version: number }) => (version === 1 ? detail(1, 'Plan the sequence.') : detail(2, 'Plan the sequence.\nCheck the voice.')),
    'skills.file': () => new TextEncoder().encode('print(1)'),
    ...extra,
  });
  render(<Routed />);
  return bridge;
}

describe('SkillsScreen', () => {
  it('maps global, project and shadowed skills with live usage, and lists them too', async () => {
    setup();
    const map = await screen.findByRole('group', { name: 'Skill map' });
    expect(within(map).getByRole('button', { name: /^email-sequence, global, version 2, used by 1$/ })).toBeTruthy();
    expect(within(map).getByRole('button', { name: /^brand-voice, global, version 1, shadowed/ })).toBeTruthy();
    expect(within(map).getByRole('button', { name: /^brand-voice, Onboarding project skill, version 3/ })).toBeTruthy();
    expect(within(map).getByRole('link', { name: 'Welcome emails' }).getAttribute('href')).toBe('#/p/p1/threads/t1');
    expect(screen.getByRole('button', { name: 'In use now · 2' })).toBeTruthy();
    fireEvent.click(screen.getByRole('button', { name: 'List' }));
    const onboarding = screen.getByRole('region', { name: 'Onboarding skills' });
    expect(onboarding.textContent).toContain('shadows global');
  });

  it('opens a skill with its change notes, compares versions and restores one', async () => {
    const bridge = setup({ 'skills.restore': () => ({ version: 3 }) });
    fireEvent.click(await screen.findByRole('button', { name: /^email-sequence, global/ }));
    await waitFor(() => expect(window.location.hash).toBe('#/skills/global%3Aemail-sequence'));
    const panel = await screen.findByRole('article', { name: 'Skill email-sequence' });
    expect(await within(panel).findByText('Added a voice check')).toBeTruthy();
    expect(panel.textContent).toContain('Welcome emails');
    fireEvent.click(within(panel).getByRole('tab', { name: /^History/ }));
    const changes = await within(panel).findByLabelText('Changes from v1 to v2');
    expect(changes.textContent).toContain('+ Check the voice.');
    fireEvent.click(within(panel).getByRole('button', { name: 'Restore' }));
    fireEvent.click(screen.getAllByRole('button', { name: 'Restore' }).at(-1)!);
    await waitFor(() => expect(bridge.calls.find((c) => c.channel === 'skills.restore')?.input).toEqual({ name: 'email-sequence', version: 1 }));
    fireEvent.click(within(panel).getByRole('tab', { name: /^Files/ }));
    fireEvent.click(within(panel).getByRole('button', { name: /scripts\/count\.py/ }));
    expect(await within(panel).findByText('print(1)')).toBeTruthy();
  });

  it('edits by hand, deletes, and asks Desk to refine', async () => {
    const bridge = setup({ 'skills.save': () => ({ version: 3 }), 'skills.remove': () => ({ ok: true }), 'projects.send': () => ({ ok: true }) });
    window.location.hash = '#/skills/global%3Aemail-sequence';
    const panel = await screen.findByRole('article', { name: 'Skill email-sequence' });
    fireEvent.click(await within(panel).findByRole('button', { name: 'Edit' }));
    const sheet = screen.getByRole('dialog', { name: 'Edit email-sequence' });
    fireEvent.change(within(sheet).getByLabelText('Instructions (SKILL.md)'), { target: { value: 'Plan it.' } });
    fireEvent.click(within(sheet).getByRole('checkbox'));
    fireEvent.change(within(sheet).getByLabelText('Change note (optional)'), { target: { value: 'Shorter' } });
    fireEvent.click(within(sheet).getByRole('button', { name: 'Save new version' }));
    await waitFor(() =>
      expect(bridge.calls.find((c) => c.channel === 'skills.save')?.input).toEqual({ name: 'email-sequence', skill: { instructions: 'Plan it.', files: [], remove_files: ['scripts/count.py'], change_note: 'Shorter' } }),
    );
    fireEvent.click(within(panel).getByRole('button', { name: 'Refine with Desk' }));
    const ask = screen.getByRole('dialog', { name: 'Refine email-sequence with Desk' });
    expect((within(ask).getByLabelText("Which project's Desk") as HTMLSelectElement).value).toBe('p1');
    fireEvent.click(within(ask).getByRole('button', { name: 'Send to Desk' }));
    await waitFor(() => expect(bridge.calls.find((c) => c.channel === 'projects.send')?.input).toEqual({ id: 'p1', text: 'Refine the skill "email-sequence":' }));
    expect(window.location.hash).toBe('#/p/p1/conversation');
    window.location.hash = '#/skills/global%3Aemail-sequence';
    const again = await screen.findByRole('article', { name: 'Skill email-sequence' });
    fireEvent.click(await within(again).findByRole('button', { name: 'Delete' }));
    fireEvent.click(screen.getAllByRole('button', { name: 'Delete' }).at(-1)!);
    await waitFor(() => expect(bridge.calls.find((c) => c.channel === 'skills.remove')?.input).toEqual({ name: 'email-sequence' }));
  });

  it('creates a project skill with an uploaded script, and imports a folder', async () => {
    const bridge = setup({ 'skills.save': () => ({ version: 1 }), 'app.pickFolder': () => '/Users/me/.claude/skills/pdf', 'skills.import': () => ({ version: 1, dir: '/data/skills/pdf', created: true, description: '' }) });
    fireEvent.click(await screen.findByRole('button', { name: 'New skill' }));
    const sheet = screen.getByRole('dialog', { name: 'New skill' });
    fireEvent.change(within(sheet).getByLabelText('Name'), { target: { value: 'Bad Name' } });
    fireEvent.click(within(sheet).getByRole('button', { name: 'Create skill' }));
    expect(await within(sheet).findByText(/lowercase letters/)).toBeTruthy();
    fireEvent.change(within(sheet).getByLabelText('Name'), { target: { value: 'receipt-sorting' } });
    fireEvent.change(within(sheet).getByLabelText('Scope'), { target: { value: 'p2' } });
    fireEvent.change(within(sheet).getByLabelText('Description'), { target: { value: 'Sort receipts' } });
    fireEvent.change(within(sheet).getByLabelText('Instructions (SKILL.md)'), { target: { value: 'Sort them.' } });
    fireEvent.change(within(sheet).getByTestId('skill-upload'), { target: { files: [new File(['hi'], 'sort.py')] } });
    fireEvent.click(within(sheet).getByRole('button', { name: 'Create skill' }));
    await waitFor(() =>
      expect(bridge.calls.find((c) => c.channel === 'skills.save')?.input).toEqual({
        projectId: 'p2',
        name: 'receipt-sorting',
        skill: { description: 'Sort receipts', instructions: 'Sort them.', files: [{ path: 'scripts/sort.py', content_base64: 'aGk=' }], remove_files: [] },
      }),
    );
    await waitFor(() => expect(window.location.hash).toBe('#/skills/project%3Ap2%3Areceipt-sorting'));

    fireEvent.click(screen.getByRole('button', { name: 'Import from ~/.claude/skills' }));
    const imp = await screen.findByRole('dialog', { name: 'Import a skill' });
    fireEvent.click(within(imp).getByRole('button', { name: 'Import' }));
    await waitFor(() => expect(bridge.calls.find((c) => c.channel === 'skills.import')?.input).toEqual({ path: '/Users/me/.claude/skills/pdf' }));
    await waitFor(() => expect(window.location.hash).toBe('#/skills/global%3Apdf'));
  });

  it("shows Desk's built-in skills above your own, and duplicates one into your skills", async () => {
    let copied = false;
    const bridge = setup({
      'builtins.list': () => [builtin('pdf-toolkit', { title: 'PDF toolkit', shadowed_by: copied ? 'global' : null }), builtin('images')],
      'builtins.get': () => ({ ...detail(1, '# PDF'), name: 'pdf-toolkit', scope: 'builtin' }),
      'builtins.duplicate': () => ((copied = true), { dir: '/s/pdf-toolkit', created: true, version: 1 }),
      'skills.history': () => [{ version: 1, description: 'd', current: true, change_note: 'Duplicated from the built-in skill', origin: 'builtin:pdf-toolkit@0123456789ab', ts: new Date().toISOString() }],
    });
    const group = await screen.findByRole('region', { name: 'Built into Desk' });
    expect(screen.getByRole('group', { name: 'Skill map' })).toBeTruthy();
    fireEvent.click(within(group).getByRole('button', { name: 'Open PDF toolkit' }));
    await waitFor(() => expect(window.location.hash).toBe('#/skills/builtin%3Apdf-toolkit'));
    const panel = await screen.findByRole('article', { name: 'Built-in skill pdf-toolkit' });
    fireEvent.click(within(panel).getByRole('button', { name: 'Duplicate to my skills' }));
    fireEvent.click(within(screen.getByRole('dialog', { name: 'Duplicate pdf-toolkit' })).getByRole('button', { name: 'Duplicate' }));
    await waitFor(() => expect(window.location.hash).toBe('#/skills/global%3Apdf-toolkit'));
    expect(bridge.calls.find((c) => c.channel === 'builtins.duplicate')?.input).toEqual({ name: 'pdf-toolkit' });
    const copy = await screen.findByRole('article', { name: 'Skill pdf-toolkit' });
    expect(await within(copy).findByText(/Customised from the built-in skill/)).toBeTruthy();
    expect(within(copy).getByRole('link', { name: 'pdf-toolkit' }).getAttribute('href')).toBe('#/skills/builtin%3Apdf-toolkit');
    expect(copy.textContent).toContain('Built into Desk · v1');
    await waitFor(() => expect(within(group).getByText('Shadowed by your global skill')).toBeTruthy());
  });

  it('shows the built-ins even with no skills of your own', async () => {
    installBridge({ 'skills.list': () => [], 'builtins.list': () => [builtin('images')] });
    render(<Routed />);
    expect(await screen.findByRole('region', { name: 'Built into Desk' })).toBeTruthy();
    expect(screen.getByText('No skills of your own yet')).toBeTruthy();
  });
});
