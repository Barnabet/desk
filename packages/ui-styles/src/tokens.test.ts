import { readdirSync, readFileSync } from 'node:fs';
import { describe, expect, it } from 'vitest';

describe('title bar drag region', () => {
  it('exempts clickable children and the switcher popover, which would otherwise drag the window instead of taking clicks', () => {
    const css = readFileSync(new URL('./tokens.css', import.meta.url), 'utf8').replace(/\/\*[\s\S]*?\*\//g, '');
    const noDrag = [...css.matchAll(/([^{}]+)\{[^}]*-webkit-app-region:\s*no-drag/g)].flatMap((m) => m[1]!.split(',').map((sel) => sel.trim()));
    for (const sel of ['.titlebar a', '.titlebar button', '.titlebar input', ".titlebar [role='dialog']"]) expect(noDrag).toContain(sel);
  });
});

/** Every place that draws UI: these shared styles, the desktop renderer, the pure view logic and the web UI. */
const ROOTS = ['./', '../../../apps/desktop/src/renderer/', '../../ui-core/src/', '../../../apps/web-ui/src/'].map((p) => new URL(p, import.meta.url));
const REPO = new URL('../../../', import.meta.url);
const COLOR_LITERAL = /#[0-9a-fA-F]{3,8}\b|rgba?\((?!var\()/;

function sources(dir: URL): URL[] {
  return readdirSync(dir, { withFileTypes: true }).flatMap((e) => {
    if (e.isDirectory()) return sources(new URL(`${e.name}/`, dir));
    return /\.(css|html|tsx?)$/.test(e.name) && !/\.(test|spec)\.tsx?$/.test(e.name) && e.name !== 'tokens.css' ? [new URL(e.name, dir)] : [];
  });
}

describe('theme', () => {
  it('keeps every color in tokens.css, so the dark theme reaches every screen', () => {
    const offenders = ROOTS.flatMap(sources).flatMap((file) =>
      readFileSync(file, 'utf8')
        .split('\n')
        .flatMap((line, i) => (COLOR_LITERAL.test(line.replace(/'#\/[^']*'|`#\/[^`]*`|href="#[^"]*"/g, '')) ? [`${file.pathname.slice(REPO.pathname.length)}:${i + 1}: ${line.trim()}`] : [])),
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

  it("keeps desk web's own pages (login, expired link) in token values, each with its dark value", () => {
    const css = readFileSync(new URL('./tokens.css', import.meta.url), 'utf8');
    const values = (block: string | undefined) => new Map([...(block ?? '').matchAll(/(--[\w-]+):\s*(#[0-9a-fA-F]{3,8})\b/g)].map((m) => [m[1]!, m[2]!.toLowerCase()]));
    const light = values(css.match(/^:root \{([\s\S]*?)^\}/m)?.[1]);
    const dark = values(css.match(/@media \(prefers-color-scheme: dark\) \{\s*:root \{([\s\S]*?)\}/)?.[1]);
    const server = new URL('../../../apps/web-server/src/', import.meta.url);
    const files = sources(server).filter((f) => f.pathname.endsWith('.ts'));
    const problems: string[] = [];
    let pages = 0;
    for (const file of files) {
      const text = readFileSync(file, 'utf8').replace(/\/\*[\s\S]*?\*\/|\/\/.*$/gm, '');
      if (/rgba?\((?!var\()/.test(text)) problems.push(`${file.pathname}: rgb() colour`);
      const darkPart = [...text.matchAll(/@media \(prefers-color-scheme: dark\)\{((?:[^{}]*\{[^{}]*\})*)\}/g)].map((m) => m[1]!).join(' ');
      const hexes = (t: string) => [...t.matchAll(/#[0-9a-fA-F]{3,8}\b/g)].map((m) => m[0].toLowerCase());
      const lightHexes = hexes(text.replace(/@media \(prefers-color-scheme: dark\)\{((?:[^{}]*\{[^{}]*\})*)\}/g, ''));
      const darkHexes = hexes(darkPart);
      if (!lightHexes.length && !darkHexes.length) continue;
      pages++;
      for (const hex of lightHexes) {
        const names = [...light].filter(([, v]) => v === hex).map(([n]) => n);
        if (!names.length) problems.push(`${file.pathname}: ${hex} is no token's light value`);
        else if (!names.some((n) => darkHexes.includes(dark.get(n) ?? ''))) problems.push(`${file.pathname}: ${hex} (${names.join(', ')}) has no dark value beside it`);
      }
      for (const hex of darkHexes) if (![...dark.values()].includes(hex)) problems.push(`${file.pathname}: ${hex} is no token's dark value`);
    }
    expect(pages).toBeGreaterThan(0);
    expect(problems).toEqual([]);
  });
});
