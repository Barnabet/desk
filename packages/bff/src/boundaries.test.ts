import { existsSync, readdirSync, readFileSync } from 'node:fs';
import { describe, expect, it } from 'vitest';

/** The non-test sources of one folder of the package. */
function sources(dir: string): Array<{ file: string; text: string }> {
  const url = new URL(`./${dir}/`, import.meta.url);
  if (!existsSync(url)) return [];
  return readdirSync(url)
    .filter((f) => f.endsWith('.ts') && !f.endsWith('.test.ts'))
    .map((f) => ({ file: `${dir}/${f}`, text: readFileSync(new URL(f, url), 'utf8') }));
}

describe('@desk/bff', () => {
  it('keeps the contract browser-safe: no Node modules or globals, and no server code', () => {
    const contract = sources('contract');
    expect(contract.map((s) => s.file)).toContain('contract/index.ts');
    for (const { file, text } of contract) {
      expect(text, file).not.toMatch(/from '(node:[^']+|electron|\.\.\/server[^']*|@desk\/client\/node)'/);
      expect(text, file).not.toMatch(/\bBuffer\b|\bNodeJS\.|\bprocess\./);
    }
  });

  it('never imports Electron', () => {
    for (const { file, text } of [...sources('contract'), ...sources('server')]) expect(text, file).not.toMatch(/from 'electron'/);
  });
});
