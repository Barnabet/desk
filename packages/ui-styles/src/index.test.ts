import { readdirSync, readFileSync } from 'node:fs';
import { createRequire } from 'node:module';
import { describe, expect, it } from 'vitest';

const dir = new URL('./', import.meta.url);
const FONTS = ['@fontsource-variable/geist', '@fontsource-variable/geist-mono', '@fontsource-variable/newsreader'];
/** The Electron renderer's cascade: tokens, then each screen's sheet (automations joined in Plan 20, after attention). */
const SHEETS = ['tokens', 'attention', 'automations', 'conversation', 'knowledge', 'map', 'settings', 'skills', 'system', 'threads'];

describe('@desk/ui-styles', () => {
  it('loads the fonts, then every sheet once, in the order the renderer always had', () => {
    const imports = [...readFileSync(new URL('index.css', dir), 'utf8').matchAll(/^@import '([^']+)';$/gm)].map((m) => m[1]);
    expect(imports).toEqual([...FONTS, ...SHEETS.map((s) => `./${s}.css`)]);
    const sheets = readdirSync(dir).filter((f) => f.endsWith('.css') && f !== 'index.css');
    expect(sheets.sort()).toEqual(SHEETS.map((s) => `${s}.css`).sort());
  });

  it('depends on the font packages it imports', () => {
    const require = createRequire(new URL('index.css', dir));
    for (const font of FONTS) expect(require.resolve(font)).toMatch(/index\.css$/);
  });
});
