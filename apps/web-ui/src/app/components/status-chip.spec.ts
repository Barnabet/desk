import { render, screen } from '@testing-library/angular';
import { describe, expect, it } from 'vitest';
import { StatusChip, statusLabel } from './status-chip';

describe('StatusChip', () => {
  it('uses calm resilience wording', () => {
    expect(statusLabel('queued', 'Recovered after daemon restart').label).toBe('Will resume');
    expect(statusLabel('queued', null).label).toBe('Queued');
    expect(statusLabel('running', null, true).label).toBe('Paused, will resume');
    expect(statusLabel('waiting', 'Waiting for approval').label).toBe('Waiting');
    expect(statusLabel('failed', 'boom').tone).toBe('fail');
  });

  it('renders the label and the reason', async () => {
    await render(StatusChip, { inputs: { status: 'waiting', reason: 'Waiting for approval' } });
    expect(screen.getByText('Waiting')).toBeTruthy();
    expect(screen.getByTitle('Waiting for approval')).toBeTruthy();
  });
});
