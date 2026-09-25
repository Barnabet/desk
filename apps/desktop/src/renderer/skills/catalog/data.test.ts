import { describe, expect, it } from 'vitest';
import { runtimeWords } from './data';

describe('runtimeWords', () => {
  it('names what Desk sets up, including a browser', () => {
    const py = { version: '3.12', packages: [] };
    expect(runtimeWords({ runtime: { python: py, extras: ['browser'] } })).toBe('Python 3.12 + browser · set up by Desk');
    expect(runtimeWords({ runtime: { python: py, extras: ['playwright-chromium'] } })).toBe('Python 3.12 + Chromium · set up by Desk');
    expect(runtimeWords({ runtime: {} })).toBe('Nothing to set up');
  });
});
