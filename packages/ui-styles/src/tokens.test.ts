import { readFileSync } from 'node:fs';
import { describe, expect, it } from 'vitest';

describe('title bar drag region', () => {
  it('exempts clickable children and the switcher popover, which would otherwise drag the window instead of taking clicks', () => {
    const css = readFileSync(new URL('./tokens.css', import.meta.url), 'utf8').replace(/\/\*[\s\S]*?\*\//g, '');
    const noDrag = [...css.matchAll(/([^{}]+)\{[^}]*-webkit-app-region:\s*no-drag/g)].flatMap((m) => m[1]!.split(',').map((sel) => sel.trim()));
    for (const sel of ['.titlebar a', '.titlebar button', '.titlebar input', ".titlebar [role='dialog']"]) expect(noDrag).toContain(sel);
  });
});
