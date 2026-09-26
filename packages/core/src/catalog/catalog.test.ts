import { describe, expect, it } from 'vitest';
import { CatalogFile } from '@desk/protocol';
import raw from './catalog.json' with { type: 'json' };

describe('the shipped catalog', () => {
  const catalog = CatalogFile.parse(raw);

  it('validates, with unique ids and fully pinned sources', () => {
    expect(catalog.entries.length).toBe(18);
    expect(new Set(catalog.entries.map((e) => e.id)).size).toBe(catalog.entries.length);
    for (const e of catalog.entries) {
      if (e.source.type === 'github') expect(e.source.sha, e.id).toMatch(/^[0-9a-f]{40}$/);
      if (e.runtime.node) expect(e.runtime.node.lock.length, e.id).toBeGreaterThan(0);
    }
  });

  it("holds only third-party skills (Desk's own are built in: skills/builtins.json)", () => {
    expect(catalog.entries.filter((e) => e.source.type === 'builtin').map((e) => e.id)).toEqual([]);
  });
});
