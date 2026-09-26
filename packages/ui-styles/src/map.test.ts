import { readFileSync } from 'node:fs';
import { describe, expect, it } from 'vitest';

describe('map canvas', () => {
  it('selects no text while the background is dragged, and only the canvas opts out of selection', () => {
    const css = readFileSync(new URL('./map.css', import.meta.url), 'utf8').replace(/\/\*[\s\S]*?\*\//g, '');
    const rules = [...css.matchAll(/([^{}]+)\{([^}]*)\}/g)].map((m) => ({ selector: m[1]!.trim(), body: m[2]! }));
    const unselectable = rules.filter((r) => /(^|[;\s])user-select:\s*none/.test(r.body));
    expect(unselectable.map((r) => r.selector)).toEqual(['.map-canvas']);
    expect(unselectable[0]!.body).toMatch(/-webkit-user-select:\s*none;/);
  });
});
