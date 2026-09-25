import { readdirSync, readFileSync } from 'node:fs';
import { describe, expect, it } from 'vitest';

const dir = new URL('./', import.meta.url);
const modules = readdirSync(dir).filter((f) => f.endsWith('.ts') && !f.endsWith('.test.ts') && f !== 'index.ts');

describe('@desk/ui-core', () => {
  it('stays framework-neutral: no React, Angular, Electron or Node, and no window or document', () => {
    expect(modules.length).toBeGreaterThan(0);
    for (const f of modules) {
      const text = readFileSync(new URL(f, dir), 'utf8');
      expect(text, f).not.toMatch(/from '(react|react-dom[^']*|electron|@angular\/[^']+|node:[^']+|@desk\/client\/node|@desk\/bff\/server)'/);
      expect(text, f).not.toMatch(/\b(window|document)\.|\bprocess\.|\bBuffer\b|\bNodeJS\./);
    }
  });

  it('exports every module from its one entry point', () => {
    const index = readFileSync(new URL('index.ts', dir), 'utf8');
    for (const f of modules) expect(index, f).toContain(`export * from './${f.replace(/\.ts$/, '')}';`);
  });
});
