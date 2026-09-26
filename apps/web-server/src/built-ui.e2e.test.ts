import { existsSync, readFileSync } from 'node:fs';
import { join } from 'node:path';
import { fileURLToPath } from 'node:url';
import { describe, expect, it } from 'vitest';
import { findInlineCode } from './static';

/** What `pnpm --filter @desk/web-ui build` writes and `desk web` serves. */
const UI = fileURLToPath(new URL('../../web-ui/dist/browser', import.meta.url));
const index = () => readFileSync(join(UI, 'index.html'), 'utf8');

describe('the built web UI (spec §4.9)', () => {
  it('has been built', () => {
    expect(existsSync(join(UI, 'index.html')), `${join(UI, 'index.html')} is missing: run pnpm --filter @desk/web-ui build`).toBe(true);
  });

  it('has no inline script body and no on*= attribute in index.html, which the CSP would block', () => {
    expect(findInlineCode(index())).toEqual([]);
  });

  it('loads its code and styles from files in the build, without the critical-CSS print trick', () => {
    const html = index();
    const refs = [...html.matchAll(/<(?:script|link)\b[^>]*?\s(?:src|href)="([^"]+)"/gi)].map((m) => m[1]!);
    expect(refs.some((r) => /^main-[A-Z0-9]+\.js$/.test(r)), refs.join(', ')).toBe(true);
    for (const r of refs) expect(existsSync(join(UI, r)), r).toBe(true);
    expect(html).not.toContain('media="print"');
  });
});
