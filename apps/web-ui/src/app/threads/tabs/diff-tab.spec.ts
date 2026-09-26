import { render, screen, waitFor } from '@testing-library/angular';
import { describe, expect, it } from 'vitest';
import type { ThreadDiff } from '@desk/protocol';
import { FakeDeskBridge } from '../../testing/fake-bridge';
import { DiffTab } from './diff-tab';

const diff: ThreadDiff = {
  base: 'abc1234',
  branch: 'desk/welcome-emails',
  files: [
    { path: 'emails/01.md', status: 'added', additions: 3, deletions: 0 },
    { path: 'logo.png', status: 'modified', additions: null, deletions: null },
  ],
  patch: 'diff --git a/emails/01.md b/emails/01.md\n--- /dev/null\n+++ b/emails/01.md\n@@ -0,0 +1,3 @@\n+# Welcome\n+\n+Hi.\n unchanged',
};

describe('DiffTab', () => {
  it('shows the branch against its base, the files with their counts, and a coloured patch', async () => {
    const bridge = new FakeDeskBridge({ 'threads.diff': () => diff });
    await render(DiffTab, { inputs: { threadId: 't', version: '5' }, providers: bridge.providers });
    const note = await screen.findByText(/2 files changed\./);
    expect(note.textContent).toBe("desk/welcome-emails against abc1234 · 2 files changed. Desk never merges; merge the branch yourself when you're happy.");
    const rows = [...document.querySelectorAll('.diff-files tr')].map((r) => [...r.querySelectorAll('td')].map((td) => td.textContent));
    expect(rows).toEqual([
      ['A', 'emails/01.md', '+3', '−0'],
      ['M', 'logo.png', 'bin', ''],
    ]);
    expect(document.querySelector('.diff-status')!.getAttribute('title')).toBe('added');
    const patch = screen.getByLabelText('Patch');
    expect(patch.textContent).toBe(`${diff.patch}\n`);
    expect([...patch.querySelectorAll('span')].map((s) => s.getAttribute('class'))).toEqual(['diff-meta', 'diff-meta', 'diff-meta', 'diff-hunk', 'diff-line-add', 'diff-line-add', 'diff-line-add', null]);
    expect(bridge.calls).toEqual([{ channel: 'threads.diff', input: { id: 't' } }]);
  });

  it('explains a scratch thread, and asks again when the thread moves on', async () => {
    const bridge = new FakeDeskBridge({
      'threads.diff': () => {
        throw { code: 'conflict', message: 'no worktree', status: 409 };
      },
    });
    const view = await render(DiffTab, { inputs: { threadId: 't', version: '5' }, providers: bridge.providers });
    expect(await screen.findByRole('heading', { name: 'No diff for this thread' })).toBeTruthy();
    expect(screen.getByText('This thread works in a scratch workspace, not on a git branch. See Files instead.')).toBeTruthy();
    await view.rerender({ inputs: { threadId: 't', version: '6' } });
    await waitFor(() => expect(bridge.calls).toHaveLength(2));
  });

  it('says it is loading, then why the diff could not load', async () => {
    let fail: (err: unknown) => void = () => {};
    const bridge = new FakeDeskBridge({ 'threads.diff': () => new Promise((_, reject) => (fail = reject)) });
    await render(DiffTab, { inputs: { threadId: 't', version: '5' }, providers: bridge.providers });
    expect(screen.getByText('Loading the diff…')).toBeTruthy();
    fail({ code: 'internal', message: 'git failed', status: 500 });
    expect(await screen.findByRole('heading', { name: "Couldn't load the diff" })).toBeTruthy();
    expect(screen.getByText('git failed')).toBeTruthy();
  });
});
