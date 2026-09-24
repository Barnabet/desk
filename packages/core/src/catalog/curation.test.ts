import { describe, expect, it } from 'vitest';
import { NodeLockEntry } from '@desk/protocol';
import { detectLicense, flattenLock, shellWord } from './curation';

const sri = 'sha512-' + 'A'.repeat(86) + '==';

describe('flattenLock', () => {
  it('keeps installed packages with their location, integrity and platform, and drops dev packages and links', () => {
    const lock = {
      lockfileVersion: 3,
      packages: {
        '': { name: 'pretty-mermaid-skill', version: '1.0.0' },
        'node_modules/beautiful-mermaid': { version: '1.1.3', integrity: sri },
        'node_modules/@resvg/resvg-js-darwin-arm64': { version: '2.6.2', integrity: sri, os: ['darwin'], cpu: ['arm64'] },
        'node_modules/beautiful-mermaid/node_modules/entities': { version: '7.0.1', integrity: sri },
        'node_modules/vitest': { version: '3.0.0', integrity: sri, dev: true },
        'node_modules/local': { link: true },
      },
    };
    const out = flattenLock(lock);
    expect(out).toEqual([
      { name: '@resvg/resvg-js-darwin-arm64', version: '2.6.2', integrity: sri, path: 'node_modules/@resvg/resvg-js-darwin-arm64', os: ['darwin'], cpu: ['arm64'] },
      { name: 'beautiful-mermaid', version: '1.1.3', integrity: sri, path: 'node_modules/beautiful-mermaid' },
      { name: 'entities', version: '7.0.1', integrity: sri, path: 'node_modules/beautiful-mermaid/node_modules/entities' },
    ]);
    for (const e of out) expect(NodeLockEntry.safeParse(e).success).toBe(true);
  });

  it('refuses old lockfiles and packages without integrity', () => {
    expect(() => flattenLock({ lockfileVersion: 1 })).toThrow(/lockfileVersion 2 or 3/);
    expect(() => flattenLock({ lockfileVersion: 3, packages: { 'node_modules/x': { version: '1.0.0' } } })).toThrow(/no version or integrity/);
  });
});

describe('detectLicense', () => {
  const md = (license?: string) => `---\nname: x\ndescription: d\n${license ? `license: ${license}\n` : ''}---\n\nBody.\n`;
  it('prefers the frontmatter, then recognises common licence texts', () => {
    expect(detectLicense(null, md('MIT'))).toBe('MIT');
    expect(detectLicense(null, md('MIT License'))).toBe('MIT');
    expect(detectLicense('Permission is hereby granted, free of charge, to any person', md())).toBe('MIT');
    expect(detectLicense('                                 Apache License\n                           Version 2.0, January 2004', md('Complete terms in LICENSE.txt'))).toBe('Apache-2.0');
    expect(detectLicense('Attribution-ShareAlike 4.0 International', md())).toBe('CC-BY-SA-4.0');
    expect(detectLicense('All rights reserved.', md())).toBeNull();
  });
});

describe('shellWord', () => {
  it('quotes only when needed', () => {
    expect(shellWord('scripts/run.py')).toBe('scripts/run.py');
    expect(shellWord("it's here")).toBe(`'it'\\''s here'`);
  });
});
