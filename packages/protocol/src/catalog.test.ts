import { describe, expect, it } from 'vitest';
import { CatalogFile, CatalogInstallRequest } from './catalog';

const entry = {
  id: 'paper-lookup',
  title: 'Paper lookup',
  category: 'research',
  summary: 'Search scholarly APIs.',
  license: 'MIT',
  homepage: 'https://github.com/o/r',
  source: { type: 'github', repo: 'o/r', path: 'skills/paper-lookup', sha: 'a'.repeat(40) },
  digest: `sha256:${'b'.repeat(64)}`,
  files: 3,
  bytes: 1200,
  runtime: { python: { version: '3.12', packages: ['requests==2.32.5', 'markitdown[all]==0.1.3'] } },
};

describe('catalog schemas', () => {
  it('parses a pinned entry and fills defaults', () => {
    const c = CatalogFile.parse({ version: 1, updated: '2026-09-24', entries: [entry, { ...entry, id: 'b', source: { type: 'builtin', path: 'word-documents' }, runtime: undefined }] });
    expect(c.entries[0]!.caveats).toEqual([]);
    expect(c.entries[1]!.runtime).toEqual({});
  });

  it('refuses loose pins and unsafe paths', () => {
    const bad = (patch: object) => CatalogFile.safeParse({ version: 1, updated: '2026-09-24', entries: [{ ...entry, ...patch }] }).success;
    expect(bad({ source: { ...entry.source, sha: 'main' } })).toBe(false);
    expect(bad({ source: { ...entry.source, path: '../x' } })).toBe(false);
    expect(bad({ runtime: { python: { version: '3.12', packages: ['requests>=2'] } } })).toBe(false);
    expect(bad({ runtime: { node: { lock: [{ name: 'x', version: '1.0.0', integrity: 'md5-x', path: 'node_modules/x' }] } } })).toBe(false);
    expect(bad({ runtime: { node: { lock: [{ name: 'x', version: '1.0.0', integrity: 'sha512-AAAA', path: '../x' }] } } })).toBe(false);
    expect(bad({ id: 'Bad_Name' })).toBe(false);
  });

  it('defaults an install to global without replacing local edits', () => {
    expect(CatalogInstallRequest.parse({})).toEqual({ scope: 'global', replace_modified: false });
  });
});
