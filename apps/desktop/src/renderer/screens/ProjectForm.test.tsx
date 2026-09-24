// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, describe, expect, it } from 'vitest';
import { installBridge } from '../test/bridge';
import { ProjectForm } from './ProjectForm';

afterEach(cleanup);

describe('ProjectForm', () => {
  it('sends settings only when More options changed them', async () => {
    const bridge = installBridge({
      'models.list': () => [{ id: 'claude-opus-5-5' }, { id: 'claude-fable-5-1' }],
      'projects.create': () => ({ project: { id: 'new' } }),
    });
    let created = '';
    render(<ProjectForm onCreated={(id) => (created = id)} />);
    fireEvent.change(screen.getByLabelText('Name'), { target: { value: 'Launch' } });
    const more = screen.getByText('More options').closest('details')!;
    more.open = true;
    fireEvent(more, new Event('toggle'));
    fireEvent.click(await screen.findByLabelText(/Minimal/));
    fireEvent.click(screen.getByRole('button', { name: 'Create project' }));
    await waitFor(() => expect(created).toBe('new'));
    expect(bridge.calls.find((c) => c.channel === 'projects.create')?.input).toMatchObject({ name: 'Launch', settings: { check_in: 'minimal', review_rounds: 2 } });
  });
});
