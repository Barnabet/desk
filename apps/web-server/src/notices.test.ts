import { describe, expect, it } from 'vitest';
import type { AttentionItem } from '@desk/protocol';
import { webNotices } from './notices';

const item = (kind: AttentionItem['kind'], id: string, title: string): AttentionItem => ({
  id,
  kind,
  project_id: 'p',
  project_name: 'Launch',
  agent_id: null,
  title,
  detail: '',
  created_at: '2026-09-25T10:00:00.000Z',
  ref: {},
});

describe('webNotices', () => {
  it("uses the desktop app's wording, tags each notice with its item and links to it in Attention", () => {
    expect(webNotices([item('question', 'question:12', 'Which market first?')])).toEqual([
      { tag: 'question:12', title: 'Launch: Desk has a question', body: 'Which market first?', route: '#/attention?item=question%3A12' },
    ]);
  });

  it('shows at most three at once', () => {
    expect(webNotices(['a', 'b', 'c', 'd'].map((id) => item('approval', id, `Run ${id}`))).map((n) => n.tag)).toEqual(['a', 'b', 'c']);
    expect(webNotices([])).toEqual([]);
  });
});
