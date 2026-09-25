import { existsSync, readdirSync, readFileSync, statSync } from 'node:fs';
import { join, relative } from 'node:path';
import { describe, expect, it } from 'vitest';

/** apps/web-ui/src (`ng test` runs in apps/web-ui). */
const SRC = existsSync(join(process.cwd(), 'src', 'app')) ? join(process.cwd(), 'src') : join(process.cwd(), 'apps', 'web-ui', 'src');
const SELF = join('app', 'security.spec.ts');

/** Ways to put markup or script into the page past Angular's escaping, and ways around its sanitizer (spec §4.10). */
const FORBIDDEN: Array<[string, RegExp]> = [
  ['[innerHTML]', /\[innerHTML\]/i],
  ['innerHTML', /\binnerHTML\b/],
  ['outerHTML', /\bouterHTML\b/],
  ['insertAdjacentHTML', /\binsertAdjacentHTML\b/],
  ['bypassSecurityTrust', /bypassSecurityTrust/],
  ['DomSanitizer', /\bDomSanitizer\b/],
  ['document.write', /\bdocument\.write(ln)?\s*\(/],
  ['createContextualFragment', /\bcreateContextualFragment\b/],
  ['srcdoc', /\bsrcdoc\b/i],
  ['eval', /\beval\s*\(/],
  ['new Function', /\bnew\s+Function\s*\(/],
];

function sources(dir: string): string[] {
  return readdirSync(dir).flatMap((name) => {
    const path = join(dir, name);
    return statSync(path).isDirectory() ? sources(path) : /\.(ts|html)$/.test(name) ? [path] : [];
  });
}

const offences = (text: string): string[] => FORBIDDEN.filter(([, re]) => re.test(text)).map(([name]) => name);

describe('agent text stays text', () => {
  it('scans every source file of the web UI', () => {
    const files = sources(SRC).map((f) => relative(SRC, f));
    expect(files).toContain('main.ts');
    expect(files).toContain('index.html');
    expect(files).toContain(join('app', 'components', 'safe-markdown.ts'));
  });

  it('recognises the ways around Angular escaping', () => {
    expect(offences('<div [innerHTML]="text"></div>')).toEqual(['[innerHTML]', 'innerHTML']);
    expect(offences('this.sanitizer.bypassSecurityTrustHtml(text)')).toEqual(['bypassSecurityTrust']);
    expect(offences('el.insertAdjacentHTML("beforeend", text); el.outerHTML = text;')).toEqual(['outerHTML', 'insertAdjacentHTML']);
    expect(offences('<iframe srcdoc="x"></iframe> eval(code) new Function(code)')).toEqual(['srcdoc', 'eval', 'new Function']);
    expect(offences('<p>{{ text }}</p> evaluate(x)')).toEqual([]);
  });

  it('never uses innerHTML, bypassSecurityTrust or their relatives under apps/web-ui/src', () => {
    const found = sources(SRC)
      .filter((f) => relative(SRC, f) !== SELF)
      .flatMap((f) => offences(readFileSync(f, 'utf8')).map((o) => `${relative(SRC, f)}: ${o}`));
    expect(found).toEqual([]);
  });
});
