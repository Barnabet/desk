import { readdirSync, readFileSync } from 'node:fs';
import { describe, expect, it } from 'vitest';

describe('title bar drag region', () => {
  it('exempts clickable children and the switcher popover, which would otherwise drag the window instead of taking clicks', () => {
    const css = readFileSync(new URL('./tokens.css', import.meta.url), 'utf8').replace(/\/\*[\s\S]*?\*\//g, '');
    const noDrag = [...css.matchAll(/([^{}]+)\{[^}]*-webkit-app-region:\s*no-drag/g)].flatMap((m) => m[1]!.split(',').map((sel) => sel.trim()));
    for (const sel of ['.titlebar a', '.titlebar button', '.titlebar input', ".titlebar [role='dialog']"]) expect(noDrag).toContain(sel);
  });
});

const RENDERER = new URL('../', import.meta.url);
const COLOR_LITERAL = /#[0-9a-fA-F]{3,8}\b|rgba?\((?!var\()/;

function sources(dir: URL): URL[] {
  return readdirSync(dir, { withFileTypes: true }).flatMap((e) => {
    if (e.isDirectory()) return sources(new URL(`${e.name}/`, dir));
    return /\.(css|tsx?)$/.test(e.name) && !/\.test\.tsx?$/.test(e.name) && e.name !== 'tokens.css' ? [new URL(e.name, dir)] : [];
  });
}

describe('theme', () => {
  it('keeps every color in tokens.css, so the dark theme reaches every screen', () => {
    const offenders = sources(RENDERER).flatMap((file) =>
      readFileSync(file, 'utf8')
        .split('\n')
        .flatMap((line, i) => (COLOR_LITERAL.test(line.replace(/'#\/[^']*'|`#\/[^`]*`|href="#[^"]*"/g, '')) ? [`${file.pathname.slice(RENDERER.pathname.length)}:${i + 1}: ${line.trim()}`] : [])),
    );
    expect(offenders).toEqual([]);
  });

  it('gives every color token a dark value', () => {
    const css = readFileSync(new URL('./tokens.css', import.meta.url), 'utf8');
    const names = (block: string | undefined) => new Set([...(block ?? '').matchAll(/(--[\w-]+):/g)].map((m) => m[1]!));
    const light = [...names(css.match(/^:root \{([\s\S]*?)^\}/m)?.[1])].filter((n) => !/^--(font|radius)/.test(n));
    const dark = names(css.match(/@media \(prefers-color-scheme: dark\) \{\s*:root \{([\s\S]*?)\}/)?.[1]);
    expect(light.length).toBeGreaterThan(20);
    expect(light.filter((n) => !dark.has(n))).toEqual([]);
  });
});
