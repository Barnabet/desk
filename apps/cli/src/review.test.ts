import { describe, expect, it } from 'vitest';
import type { CatalogReview } from '@desk/protocol';
import { renderReview } from './commands';

const review = (runtime: CatalogReview['entry']['runtime']): CatalogReview => ({
  entry: {
    id: 'web-research',
    title: 'Web research',
    category: 'research',
    summary: 'Search and read the web.',
    license: 'MIT',
    homepage: 'https://example.com',
    source: { type: 'builtin', path: 'web-research' },
    digest: `sha256:${'0'.repeat(64)}`,
    files: 1,
    bytes: 1,
    scripts: 0,
    runtime,
    caveats: [],
  },
  source_url: null,
  files: [],
  skill_md: '',
  license_text: null,
  warnings: [],
});

describe('renderReview', () => {
  it('says what Desk sets up, browser extras included', () => {
    const setsUp = (runtime: CatalogReview['entry']['runtime']) => renderReview(review(runtime)).split('\n').find((l) => l.startsWith('Sets up:'));
    const py = { version: '3.12', packages: ['playwright==1.55.0'] };
    expect(setsUp({ python: py, extras: ['browser'] })).toBe('Sets up:  Python 3.12 + browser (playwright==1.55.0)');
    expect(setsUp({ python: py, extras: ['playwright-chromium'] })).toBe('Sets up:  Python 3.12 + Chromium (playwright==1.55.0)');
    expect(setsUp({})).toBe('Sets up:  nothing');
  });
});
