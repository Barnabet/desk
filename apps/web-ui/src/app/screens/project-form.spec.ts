import { fireEvent, render, screen, waitFor } from '@testing-library/angular';
import userEvent from '@testing-library/user-event';
import { describe, expect, it } from 'vitest';
import { DeskBridge } from '../core/desk-bridge';
import { FakeDeskBridge } from '../testing/fake-bridge';
import { ProjectForm } from './project-form';

async function setup(handlers: ConstructorParameters<typeof FakeDeskBridge>[0], inputs: { cancelable?: boolean } = {}) {
  const bridge = new FakeDeskBridge(handlers);
  const created: string[] = [];
  let cancelled = 0;
  await render(ProjectForm, {
    inputs,
    providers: [{ provide: DeskBridge, useValue: bridge }],
    on: { created: (id: string) => created.push(id), cancelled: () => (cancelled += 1) },
    // The host must be the <form> its selector names (TestBed uses a <div> otherwise), or "Create project" submits nothing.
    configureTestBed: (testBed) => testBed.configureTestingModule({ inferTagName: true }),
  });
  return { bridge, created, cancelled: () => cancelled, user: userEvent.setup() };
}

describe('ProjectForm', () => {
  it('sends settings only when More options changed them', async () => {
    const { bridge, created, user } = await setup({
      'models.list': () => [{ id: 'claude-opus-5-5' }, { id: 'claude-fable-5-1' }],
      'projects.create': () => ({ project: { id: 'new' } }),
    });
    await user.type(screen.getByLabelText('Name'), 'Launch');
    const more = screen.getByText('More options').closest('details')!;
    more.open = true;
    fireEvent(more, new Event('toggle'));
    await user.click(await screen.findByLabelText(/Minimal/));
    await user.click(screen.getByRole('button', { name: 'Create project' }));
    await waitFor(() => expect(created).toEqual(['new']));
    expect(bridge.calls.find((c) => c.channel === 'projects.create')?.input).toMatchObject({ name: 'Launch', settings: { check_in: 'minimal', review_rounds: 2 } });
  });

  it('asks for a name before creating anything, and sends only what was filled in', async () => {
    const { bridge, created, user } = await setup({ 'projects.create': () => ({ project: { id: 'p1' } }) });
    await user.click(screen.getByRole('button', { name: 'Create project' }));
    expect((await screen.findByRole('alert')).textContent).toContain('Give the project a name.');
    expect(bridge.calls.some((c) => c.channel === 'projects.create')).toBe(false);
    await user.type(screen.getByLabelText('Name'), '  Launch  ');
    await user.type(screen.getByLabelText('Goal'), 'Relaunch onboarding next month');
    await user.click(screen.getByRole('button', { name: 'Create project' }));
    await waitFor(() => expect(created).toEqual(['p1']));
    expect(bridge.calls.find((c) => c.channel === 'projects.create')?.input).toEqual({ name: 'Launch', goal: 'Relaunch onboarding next month' });
  });

  it('adds each picked folder once, and removes it', async () => {
    const picks = ['/Users/me/repo', '/Users/me/repo', null];
    const { bridge, user } = await setup({ 'app.pickFolder': () => picks.shift() ?? null });
    expect(screen.getByText('Folders or git repositories Desk and its threads can read. Optional.')).toBeTruthy();
    for (let i = 0; i < 3; i++) await user.click(screen.getByRole('button', { name: 'Add folder…' }));
    expect(await screen.findByText('/Users/me/repo')).toBeTruthy();
    expect(screen.getAllByRole('listitem')).toHaveLength(1);
    expect(bridge.calls.filter((c) => c.channel === 'app.pickFolder').map((c) => c.input)).toEqual([{ purpose: 'source' }, { purpose: 'source' }, { purpose: 'source' }]);
    await user.click(screen.getByRole('button', { name: 'Remove /Users/me/repo' }));
    await waitFor(() => expect(screen.queryByText('/Users/me/repo')).toBeNull());
    expect(screen.getByText('Folders or git repositories Desk and its threads can read. Optional.')).toBeTruthy();
  });

  it("shows deskd's refusal in the form and keeps what was typed", async () => {
    const { created, user } = await setup({
      'app.pickFolder': () => '/Users/me',
      'projects.create': () => {
        throw { code: 'validation', message: 'A project source cannot be your home folder: /Users/me', status: 400 };
      },
    });
    await user.type(screen.getByLabelText('Name'), 'Launch');
    await user.click(screen.getByRole('button', { name: 'Add folder…' }));
    await screen.findByText('/Users/me');
    await user.click(screen.getByRole('button', { name: 'Create project' }));
    expect((await screen.findByRole('alert')).textContent).toContain('A project source cannot be your home folder: /Users/me');
    expect((screen.getByLabelText('Name') as HTMLInputElement).value).toBe('Launch');
    expect(created).toEqual([]);
  });

  it('offers no Cancel by default', async () => {
    await setup({});
    expect(screen.queryByRole('button', { name: 'Cancel' })).toBeNull();
  });

  it('offers Cancel when it can be cancelled (the new-project sheet)', async () => {
    const { cancelled, user } = await setup({}, { cancelable: true });
    await user.click(screen.getByRole('button', { name: 'Cancel' }));
    expect(cancelled()).toBe(1);
  });
});
