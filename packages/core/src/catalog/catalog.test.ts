import { join } from 'node:path';
import { describe, expect, it } from 'vitest';
import { CatalogFile } from '@desk/protocol';
import raw from './catalog.json' with { type: 'json' };
import { readTree, treeDigest } from './digest';

const BUILTIN = join(import.meta.dirname, '..', '..', '..', '..', 'catalog', 'skills');

describe('the shipped catalog', () => {
  const catalog = CatalogFile.parse(raw);

  it('validates, with unique ids and fully pinned sources', () => {
    expect(catalog.entries.length).toBe(20);
    expect(new Set(catalog.entries.map((e) => e.id)).size).toBe(catalog.entries.length);
    for (const e of catalog.entries) {
      if (e.source.type === 'github') expect(e.source.sha, e.id).toMatch(/^[0-9a-f]{40}$/);
      if (e.runtime.node) expect(e.runtime.node.lock.length, e.id).toBeGreaterThan(0);
    }
  });

  it('pins builtin skills to the files in catalog/skills (run pnpm catalog:pin after editing them)', () => {
    for (const e of catalog.entries) {
      if (e.source.type !== 'builtin') continue;
      const files = readTree(join(BUILTIN, e.source.path));
      expect(treeDigest(files), e.id).toBe(e.digest);
      expect(files.length, e.id).toBe(e.files);
    }
  });
});
