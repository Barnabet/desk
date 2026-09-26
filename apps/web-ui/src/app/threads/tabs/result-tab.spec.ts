import { render, screen } from '@testing-library/angular';
import { describe, expect, it } from 'vitest';
import type { ThreadView } from '@desk/client';
import { FakeDeskBridge } from '../../testing/fake-bridge';
import { ResultTab } from './result-tab';

const thread = (extra: Partial<ThreadView> = {}) => ({ id: 't', title: 'Welcome emails', status: 'done', result_summary: null, result_artifacts: null, ...extra }) as unknown as ThreadView;

describe('ResultTab', () => {
  it('says there is no result until the thread reports', async () => {
    await render(ResultTab, { inputs: { projectId: 'p', thread: thread() }, providers: new FakeDeskBridge().providers });
    expect(screen.getByRole('heading', { name: 'No result yet' })).toBeTruthy();
    expect(screen.getByText('The thread reports here when it finishes. Desk reviews it and may send it back.')).toBeTruthy();
  });

  it('shows the report and links its artifacts to the Library', async () => {
    await render(ResultTab, {
      inputs: { projectId: 'p', thread: thread({ result_summary: 'Five drafts published', result_artifacts: ['emails/01.md', 'emails/02.md'] }) },
      providers: new FakeDeskBridge().providers,
    });
    expect(screen.getByText('Five drafts published')).toBeTruthy();
    expect(screen.getByRole('heading', { name: 'Artifacts' })).toBeTruthy();
    expect(screen.getAllByRole('link').map((a) => [a.textContent, a.getAttribute('href')])).toEqual([
      ['emails/01.md', '#/p/p/library?file=emails%2F01.md'],
      ['emails/02.md', '#/p/p/library?file=emails%2F02.md'],
    ]);
    expect(document.querySelector('.tab-body.result-tab')).toBeTruthy();
  });
});
